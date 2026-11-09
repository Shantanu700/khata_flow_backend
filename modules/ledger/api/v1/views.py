from dataclasses import asdict

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.response import Response

from base.decorators import extend_response
from base.views import BaseAV, BaseViewSet
from modules.ledger import services as ledger
from modules.ledger.api.v1.serializers import EntryFilterSerializer, EntrySerializer, SummarySerializer
from modules.ledger.models import Entry
from modules.shops.choices import PartyKind
from modules.shops.services import shop_for

FILTER_LOOKUPS = {
    "kind": "kind",
    "party": "party_id",
    "product": "product_id",
    "date_from": "occurred_on__gte",
    "date_to": "occurred_on__lte",
}


class EntryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, BaseViewSet):
    serializer_class = EntrySerializer

    def get_queryset(self):
        entries = (
            Entry.objects.filter(shop=shop_for(self.request.user))
            .select_related("party", "product", "reversal")
            .order_by("-id")
        )
        if self.action != "list":
            return entries
        filters = EntryFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        return entries.filter(**{FILTER_LOOKUPS[key]: value for key, value in filters.validated_data.items()})

    @extend_schema(parameters=[EntryFilterSerializer], methods=["get"])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request=None, responses={201: EntrySerializer})
    @action(detail=True, methods=["post"])
    def undo(self, request, pk=None):
        return Response(EntrySerializer(ledger.reverse_entry(self.get_object())).data, status=201)


class SummaryAV(BaseAV):
    "Dashboard header: what is owed, what is running low, what staff are due."

    @extend_response(SummarySerializer())
    def get(self, request):
        shop = shop_for(request.user)
        today = timezone.localdate()
        staff = shop.parties.filter(kind=PartyKind.STAFF, is_active=True).order_by("name")
        return {
            "total_outstanding": ledger.total_outstanding(shop),
            "top_debtors": [{"id": p.pk, "name": p.name, "balance": p.balance} for p in ledger.top_debtors(shop)],
            "low_stock": [
                {"id": p.pk, "name": p.name, "stock_level": p.stock_level, "threshold": p.low_stock_threshold}
                for p in ledger.low_stock_products(shop)
            ],
            "staff_dues": [{"id": p.pk, "name": p.name, **asdict(ledger.staff_due(p, today))} for p in staff],
            "today_entry_count": ledger.live_entries().filter(shop=shop, occurred_on=today).count(),
        }
