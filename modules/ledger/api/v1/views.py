from dataclasses import asdict
from datetime import timedelta

from django.db.models import Case, DecimalField, F, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.response import Response

from base.decorators import extend_response
from base.views import BaseAV, BaseViewSet
from modules.ledger import services as ledger
from modules.ledger.api.v1.serializers import (
    EntryCreateSerializer,
    EntryFilterSerializer,
    EntrySerializer,
    SummarySerializer,
)
from modules.ledger.choices import CUSTOMER_MONEY_KINDS, STAFF_MONEY_KINDS, STOCK_KINDS, EntryKind
from modules.ledger.models import Entry
from modules.shops.choices import PartyKind
from modules.shops.models import Party, Product
from modules.shops.services import shop_for

FILTER_LOOKUPS = {
    "party": "party_id",
    "product": "product_id",
    "date_from": "occurred_on__gte",
    "date_to": "occurred_on__lte",
}


def _running_balance():
    "The customer's balance after each live udhaar/vasooli entry: everything up to it, in date then id order."
    upto = (
        ledger.live_entries()
        .filter(party=OuterRef("party"), kind__in=CUSTOMER_MONEY_KINDS)
        .filter(Q(occurred_on__lt=OuterRef("occurred_on")) | Q(occurred_on=OuterRef("occurred_on"), id__lte=OuterRef("id")))
        .values("party")
        .annotate(total=Sum(Case(When(kind=EntryKind.CREDIT_GIVEN, then=F("amount")), default=-F("amount"))))
        .values("total")
    )
    live = Q(reversal__isnull=True, kind__in=CUSTOMER_MONEY_KINDS)
    return Case(When(live, then=Subquery(upto)), default=None, output_field=DecimalField(max_digits=14, decimal_places=2))


def _search(entries, q: str):
    q = q.strip()
    match = Q(party__name__icontains=q) | Q(product__name__icontains=q) | Q(note__icontains=q)
    if q.lstrip("#").isdigit():
        match |= Q(id=int(q.lstrip("#")))
    return entries.filter(match)


class EntryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, BaseViewSet):
    serializer_class = EntrySerializer

    def get_queryset(self):
        entries = (
            Entry.objects.filter(shop=shop_for(self.request.user))
            .select_related("party", "product", "reversal", "voice_command")
            .order_by("-id")
        )
        if self.action != "list":
            return entries
        filters = EntryFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        f = dict(filters.validated_data)
        kinds, q, ordering = f.pop("kind", None), f.pop("q", None), f.pop("ordering", "-id")
        entries = entries.filter(**{FILTER_LOOKUPS[key]: value for key, value in f.items()})
        if kinds:
            entries = entries.filter(kind__in=kinds)
        if q:
            entries = _search(entries, q)
        if "party" in f:
            entries = entries.annotate(running_balance=_running_balance())
        return entries.order_by(ordering, "-id") if ordering not in ("-id", "id") else entries.order_by(ordering)

    @extend_schema(parameters=[EntryFilterSerializer], methods=["get"])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request=EntryCreateSerializer, responses={201: EntrySerializer})
    def create(self, request, *args, **kwargs):
        shop = shop_for(request.user)
        body = EntryCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = dict(body.validated_data)
        party_id, product_id = data.pop("party", None), data.pop("product", None)
        party = get_object_or_404(Party, pk=party_id, shop=shop) if party_id else None
        product = get_object_or_404(Product, pk=product_id, shop=shop) if product_id else None
        kind, amount = data["kind"], data.get("amount")
        month = data.get("occurred_on") or timezone.localdate()
        if kind == EntryKind.PAYMENT_RECEIVED and party and amount and amount > ledger.customer_balance(party):
            balance = ledger.customer_balance(party)
            raise ledger.LedgerError("OVERPAYMENT", f"Only {ledger.format_inr(max(balance, 0))} is owed.")
        if kind in STAFF_MONEY_KINDS and party and amount and amount > ledger.salary_left(party, month):
            left = ledger.salary_left(party, month)
            raise ledger.LedgerError("EXCEEDS_SALARY", f"Only {ledger.format_inr(max(left, 0))} of this month's salary is left.")
        entry = ledger.record_entry(
            shop,
            kind,
            party=party,
            product=product,
            amount=amount,
            quantity=data.get("quantity"),
            occurred_on=data.get("occurred_on"),
            note=data.get("note", ""),
        )
        return Response(EntrySerializer(entry).data, status=201)

    @extend_schema(request=None, responses={201: EntrySerializer})
    @action(detail=True, methods=["post"])
    def undo(self, request, pk=None):
        return Response(EntrySerializer(ledger.reverse_entry(self.get_object())).data, status=201)


class SummaryAV(BaseAV):
    "Dashboard: what is owed, the last 14 days of udhaar and vasooli, what is running low, what staff are due."

    @extend_response(SummarySerializer())
    def get(self, request):
        shop = shop_for(request.user)
        today = timezone.localdate()
        start = today - timedelta(13)
        staff = shop.parties.filter(kind=PartyKind.STAFF, is_active=True).order_by("name")
        live = ledger.live_entries().filter(shop=shop)
        money = live.filter(kind__in=CUSTOMER_MONEY_KINDS, occurred_on__range=(start, today))
        per_day = {
            row["occurred_on"]: row
            for row in money.values("occurred_on").annotate(
                credit=Coalesce(Sum("amount", filter=Q(kind=EntryKind.CREDIT_GIVEN)), Value(ledger.ZERO), output_field=ledger.MONEY),
                payment=Coalesce(Sum("amount", filter=Q(kind=EntryKind.PAYMENT_RECEIVED)), Value(ledger.ZERO), output_field=ledger.MONEY),
            )
        }
        daily = [
            {
                "date": day,
                "credit": per_day.get(day, {}).get("credit", ledger.ZERO),
                "payment": per_day.get(day, {}).get("payment", ledger.ZERO),
            }
            for day in (start + timedelta(i) for i in range(14))
        ]
        week = [d for d in daily if d["date"] > today - timedelta(7)]
        customers = ledger.with_balance(shop.parties.filter(kind=PartyKind.CUSTOMER))
        recent = (
            Entry.objects.filter(shop=shop)
            .exclude(kind__in=STOCK_KINDS)
            .select_related("party", "product", "reversal", "voice_command")
            .order_by("-id")[:6]
        )
        return {
            "total_outstanding": ledger.total_outstanding(shop),
            "owing_count": customers.filter(balance__gt=0).count(),
            "customer_count": customers.count(),
            "top_debtors": [{"id": p.pk, "name": p.name, "balance": p.balance} for p in ledger.top_debtors(shop)],
            "low_stock": [
                {"id": p.pk, "name": p.name, "unit": p.unit, "stock_level": p.stock_level, "threshold": p.low_stock_threshold}
                for p in ledger.low_stock_products(shop)
            ],
            "staff_dues": [{"id": p.pk, "name": p.name, **asdict(ledger.staff_due(p, today))} for p in staff],
            "today_entry_count": live.filter(occurred_on=today).count(),
            "daily": daily,
            "week_credit": sum((d["credit"] for d in week), ledger.ZERO),
            "week_payment": sum((d["payment"] for d in week), ledger.ZERO),
            "entry_count_14d": live.filter(occurred_on__range=(start, today)).count(),
            "recent": EntrySerializer(recent, many=True).data,
        }
