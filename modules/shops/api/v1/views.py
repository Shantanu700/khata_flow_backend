from rest_framework import mixins

from base.enums import Status
from base.views import BaseViewSet
from modules.ledger import services as ledger
from modules.shops.api.v1.serializers import PartySerializer, ProductSerializer
from modules.shops.choices import PartyKind
from modules.shops.services import shop_for


class ShopScopedViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    BaseViewSet,
):
    "Rows of the requesting user's shop only. DELETE is a soft delete, since ledger entries point at these rows."

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

    def get_queryset(self):
        parties = ledger.with_balance(shop_for(self.request.user).parties.all()).order_by("name")
        kind = self.request.query_params.get("kind")
        if kind in PartyKind.values:
            parties = parties.filter(kind=kind)
        return parties


class ProductViewSet(ShopScopedViewSet):
    serializer_class = ProductSerializer

    def get_queryset(self):
        return ledger.with_stock_level(shop_for(self.request.user).products.all()).order_by("name")
