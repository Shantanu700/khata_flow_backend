from dataclasses import asdict
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from base.model import ModelBase
from base.serializers import BaseSerializer
from modules.ledger import services as ledger
from modules.shops.choices import PartyKind
from modules.shops.models import Party, Product


class ShopScopedNameMixin:
    "(shop, name) is unique; check it here so a duplicate is a 400, not an IntegrityError."

    def validate_name(self, value: str) -> str:
        value = value.strip()
        rows = self.Meta.model.objects.filter(shop=self.context["shop"], name__iexact=value)
        if self.instance is not None:
            rows = rows.exclude(pk=self.instance.pk)
        if rows.exists():
            raise serializers.ValidationError("That name is already used in this shop.")
        return value


def _aliases(value: list) -> list[str]:
    if not all(isinstance(alias, str) for alias in value):
        raise serializers.ValidationError("Aliases must be a list of strings.")
    return [alias.strip().lower() for alias in value if alias.strip()]


class PartySerializer(ShopScopedNameMixin, BaseSerializer):
    balance = serializers.SerializerMethodField()
    staff_due = serializers.SerializerMethodField()

    class Meta:
        model = Party
        fields = ModelBase.BASE_MODEL_FIELDS + ("name", "aliases", "kind", "monthly_salary", "is_active", "balance", "staff_due")
        read_only_fields = ModelBase.BASE_MODEL_FIELDS

    def validate_aliases(self, value: list) -> list[str]:
        return _aliases(value)

    def get_balance(self, party: Party) -> Decimal | None:
        if party.kind != PartyKind.CUSTOMER:
            return None
        balance = getattr(party, "balance", None)
        return ledger.customer_balance(party) if balance is None else balance

    def get_staff_due(self, party: Party) -> dict | None:
        if party.kind != PartyKind.STAFF:
            return None
        return asdict(ledger.staff_due(party, timezone.localdate()))


class ProductSerializer(ShopScopedNameMixin, BaseSerializer):
    stock_level = serializers.SerializerMethodField()
    is_low = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ModelBase.BASE_MODEL_FIELDS + (
            "name", "aliases", "unit", "low_stock_threshold", "unit_price", "is_active", "stock_level", "is_low"
        )
        read_only_fields = ModelBase.BASE_MODEL_FIELDS

    def validate_aliases(self, value: list) -> list[str]:
        return _aliases(value)

    def get_stock_level(self, product: Product) -> Decimal:
        level = getattr(product, "stock_level", None)
        return ledger.stock_level(product) if level is None else level

    def get_is_low(self, product: Product) -> bool:
        return self.get_stock_level(product) <= product.low_stock_threshold
