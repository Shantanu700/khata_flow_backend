from datetime import date

from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from django.db.models import F, Max, Min, Q, TextField
from django.db.models.functions import Cast
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from base.decorators import extend_response
from base.enums import Status
from base.views import BaseAV, BaseViewSet
from modules.ledger import services as ledger
from modules.ledger.choices import CUSTOMER_MONEY_KINDS
from modules.shops import photos
from modules.shops.api.v1.serializers import (
    AttendanceSerializer,
    LoginSerializer,
    MeSerializer,
    PartyFilterSerializer,
    PartySerializer,
    ProductFilterSerializer,
    ProductSerializer,
    RegisterSerializer,
    SessionSerializer,
    ShopSerializer,
    StaffMonthSerializer,
)
from modules.shops.models import Shop
from modules.shops.services import shop_for

# Updates are PUT with the full object; PATCH is not offered anywhere.
NO_PATCH = ["get", "post", "put", "delete", "head", "options"]


def _month(value: str | None) -> date:
    if not value:
        return timezone.localdate()
    year, month = value.split("-")
    return date(int(year), int(month), 1)


def _search(rows, q: str):
    "Name or any alias, case-insensitively."
    return rows.annotate(aliases_text=Cast("aliases", TextField())).filter(
        Q(name__icontains=q) | Q(aliases_text__icontains=q.lower())
    )


class ShopScopedViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    BaseViewSet,
):
    "Rows of the requesting user's shop only. DELETE is a soft delete, since ledger entries point at these rows."

    http_method_names = NO_PATCH

    def get_serializer_context(self) -> dict:
        return {**super().get_serializer_context(), "shop": shop_for(self.request.user)}

    def perform_create(self, serializer) -> None:
        serializer.save(shop=shop_for(self.request.user))

    def perform_update(self, serializer) -> None:
        serializer.save(status=Status.UPDATED)

    def perform_destroy(self, instance) -> None:
        instance.soft_delete()


class PartyViewSet(ShopScopedViewSet):
    serializer_class = PartySerializer

    def _filters(self) -> dict:
        if self.action != "list":
            return {}
        filters = PartyFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        return filters.validated_data

    def get_serializer_context(self) -> dict:
        return {**super().get_serializer_context(), "month": _month(self._filters().get("month"))}

    def get_queryset(self):
        f = self._filters()
        parties = ledger.with_balance(shop_for(self.request.user).parties.all()).annotate(
            last_entry_on=Max(
                "entries__occurred_on",
                filter=Q(entries__reversal__isnull=True, entries__kind__in=CUSTOMER_MONEY_KINDS),
            ),
            # Entries can be back-dated, so history may start before the party was added.
            first_entry_on=Min("entries__occurred_on"),
        )
        if f.get("kind"):
            parties = parties.filter(kind=f["kind"])
        if f.get("q"):
            parties = _search(parties, f["q"])
        if f.get("balance") == "owing":
            parties = parties.filter(balance__gt=0)
        elif f.get("balance") == "clear":
            parties = parties.filter(balance__lte=0)
        if f.get("advance"):
            parties = ledger.with_month_advances(parties, _month(f.get("month")))
            parties = parties.filter(month_advances__gt=0) if f["advance"] == "yes" else parties.filter(month_advances=0)
        ordering = f.get("ordering", "name")
        tail = "name" if ordering.lstrip("-") != "name" else "id"
        if ordering == "-last_entry_on":
            return parties.order_by(F("last_entry_on").desc(nulls_last=True), "name")
        return parties.order_by(ordering, tail)

    @extend_schema(parameters=[PartyFilterSerializer], methods=["get"])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(methods=["get"], responses={(200, "image/*"): OpenApiTypes.BINARY})
    @extend_schema(methods=["put"], request={"multipart/form-data": photos.PhotoSerializer}, responses={200: PartySerializer})
    @extend_schema(methods=["delete"], responses={200: PartySerializer})
    @action(detail=True, methods=["get", "put", "delete"], parser_classes=[MultiPartParser])
    def photo(self, request, pk=None):
        "The party's photo, served only to the shop's owner. PUT replaces it, DELETE removes it."
        party = self.get_object()
        if request.method == "GET":
            return photos.serve(party)
        if request.method == "PUT":
            photos.put(party, request.data)
        else:
            photos.remove(party)
        return Response(self.get_serializer(party).data)

    @extend_schema(request=AttendanceSerializer, responses={200: PartySerializer})
    @action(detail=True, methods=["put"])
    def attendance(self, request, pk=None):
        "Set one day: present, half day or absent (this month and last month). Changes are written as corrections."
        party = self.get_object()
        data = AttendanceSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        ledger.set_attendance(party, data.validated_data["on"], data.validated_data["mark"])
        return Response(self.get_serializer(self.get_object()).data)

    @extend_schema(parameters=[OpenApiParameter("month", str, description="YYYY-MM; default this month")], responses={200: StaffMonthSerializer})
    @action(detail=True, methods=["get"])
    def month(self, request, pk=None):
        "A staff member's month: pay worked out so far and the days present (for the attendance calendar)."
        party = self.get_object()
        month = _month(request.query_params.get("month"))
        start, end = ledger.month_bounds(month)
        due = ledger.staff_due(party, month)
        return Response(
            StaffMonthSerializer(
                {
                    "month": month.strftime("%Y-%m"),
                    **due.__dict__,
                    "monthly_salary": party.monthly_salary,
                    "present_days": ledger.present_days(party, start, end),
                    "half_days": ledger.half_days(party, start, end),
                }
            ).data
        )


class ProductViewSet(ShopScopedViewSet):
    serializer_class = ProductSerializer

    def get_queryset(self):
        products = ledger.with_stock_level(shop_for(self.request.user).products.all())
        if self.action != "list":
            return products
        filters = ProductFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        f = filters.validated_data
        if f.get("q"):
            products = _search(products, f["q"])
        if f.get("unit"):
            products = products.filter(unit=f["unit"])
        if f.get("low") is not None:
            low = Q(stock_level__lte=F("low_stock_threshold"))
            products = products.filter(low) if f["low"] else products.exclude(low)
        ordering = f.get("ordering", "name")
        return products.order_by(ordering, "name" if ordering.lstrip("-") != "name" else "id")

    @extend_schema(parameters=[ProductFilterSerializer], methods=["get"])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


def _session(user, token: str | None = None) -> dict:
    body = {"username": user.username, "shop": ShopSerializer(shop_for(user)).data}
    return {**body, "token": token} if token else body


class MePhotoAV(BaseAV):
    "The shop's photo, served only to its owner. PUT replaces it, DELETE removes it."

    http_method_names = ["get", "put", "delete", "options"]
    parser_classes = [MultiPartParser]

    @extend_schema(responses={(200, "image/*"): OpenApiTypes.BINARY})
    def get(self, request):
        return photos.serve(shop_for(request.user))

    @extend_schema(request={"multipart/form-data": photos.PhotoSerializer})
    @extend_response(MeSerializer())
    def put(self, request):
        photos.put(shop_for(request.user), request.data)
        return _session(request.user)

    @extend_schema(request=None)
    @extend_response(MeSerializer())
    def delete(self, request):
        photos.remove(shop_for(request.user))
        return _session(request.user)


class LoginAV(BaseAV):
    "Exchange a username and password for the API token (the web app keeps it in an httpOnly cookie)."

    authentication = False
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(request=LoginSerializer)
    @extend_response(SessionSerializer())
    def post(self, request):
        body = LoginSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        user = authenticate(request, **body.validated_data)
        if user is None:
            raise AuthenticationFailed("Wrong username or password.", code="invalid_credentials")
        token, _ = Token.objects.get_or_create(user=user)
        return _session(user, token.key)


class RegisterAV(BaseAV):
    "Open a new shop: creates the owner, the shop and the API token in one go."

    authentication = False
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(request=RegisterSerializer)
    @extend_response(SessionSerializer())
    def post(self, request):
        body = RegisterSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        with transaction.atomic():
            user = get_user_model().objects.create_user(username=data["username"], password=data["password"])
            Shop.objects.create(owner=user, name=data["shop_name"], preferred_language=data["preferred_language"])
            token = Token.objects.create(user=user)
        return _session(user, token.key)


class LogoutAV(BaseAV):
    "Delete the caller's token: every client using it is signed out."

    @extend_schema(request=None, responses={201: OpenApiTypes.OBJECT})
    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return self.success("Signed out.")


class MeAV(BaseAV):
    "The signed-in owner and their shop. PUT changes the shop's language (screens and voice)."

    http_method_names = ["get", "put", "options"]

    @extend_response(MeSerializer())
    def get(self, request):
        return _session(request.user)

    @extend_schema(request=MeSerializer)
    @extend_response(MeSerializer())
    def put(self, request):
        shop = shop_for(request.user)
        body = MeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        shop_body = ShopSerializer(shop, data=body.validated_data["shop"])
        shop_body.is_valid(raise_exception=True)
        shop_body.save(status=Status.UPDATED)
        return _session(request.user)
