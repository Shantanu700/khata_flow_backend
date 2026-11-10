from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from base.model import ModelBase
from base.serializers import BaseSerializer
from modules.ledger import services as ledger
from modules.shops.choices import SHOP_LANGUAGES, PartyKind, Unit
from modules.shops.models import Party, Product, Shop


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


class StaffDueFieldsSerializer(serializers.Serializer):
    days_present = serializers.IntegerField()
    days_in_month = serializers.IntegerField()
    earned = serializers.DecimalField(max_digits=14, decimal_places=2)
    advances = serializers.DecimalField(max_digits=14, decimal_places=2)
    salary_paid = serializers.DecimalField(max_digits=14, decimal_places=2)
    due = serializers.DecimalField(max_digits=14, decimal_places=2)


class PartySerializer(ShopScopedNameMixin, BaseSerializer):
    aliases = serializers.ListField(child=serializers.CharField(max_length=100), required=False)
    balance = serializers.SerializerMethodField()
    staff_due = serializers.SerializerMethodField()
    present_today = serializers.SerializerMethodField()
    attendance_14d = serializers.SerializerMethodField()
    last_entry_on = serializers.DateField(read_only=True, allow_null=True, default=None)
    first_entry_on = serializers.DateField(read_only=True, allow_null=True, default=None)

    class Meta:
        model = Party
        fields = ModelBase.BASE_MODEL_FIELDS + (
            "name", "aliases", "kind", "monthly_salary", "is_active", "balance", "staff_due", "present_today",
            "attendance_14d", "last_entry_on", "first_entry_on",
        )
        read_only_fields = ModelBase.BASE_MODEL_FIELDS

    def validate_aliases(self, value: list) -> list[str]:
        return _aliases(value)

    def get_balance(self, party: Party) -> Decimal | None:
        if party.kind != PartyKind.CUSTOMER:
            return None
        balance = getattr(party, "balance", None)
        return ledger.customer_balance(party) if balance is None else balance

    @extend_schema_field(StaffDueFieldsSerializer(allow_null=True))
    def get_staff_due(self, party: Party) -> dict | None:
        if party.kind != PartyKind.STAFF:
            return None
        return asdict(ledger.staff_due(party, self.context.get("month") or timezone.localdate()))

    def get_present_today(self, party: Party) -> int | None:
        "The id of today's attendance entry (undo it to un-mark), or null."
        if party.kind != PartyKind.STAFF:
            return None
        return ledger.attendance_entry_id(party, timezone.localdate())

    def get_attendance_14d(self, party: Party) -> list[str] | None:
        if party.kind != PartyKind.STAFF:
            return None
        today = timezone.localdate()
        return [d.isoformat() for d in ledger.present_days(party, today - timedelta(13), today)]


class ProductSerializer(ShopScopedNameMixin, BaseSerializer):
    aliases = serializers.ListField(child=serializers.CharField(max_length=100), required=False)
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


class PartyFilterSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=PartyKind.choices, required=False)
    q = serializers.CharField(required=False, max_length=100)
    balance = serializers.ChoiceField(choices=["owing", "clear"], required=False, help_text="Customers only.")
    advance = serializers.ChoiceField(choices=["yes", "no"], required=False, help_text="Staff: advance taken this month.")
    month = serializers.RegexField(r"^\d{4}-\d{2}$", required=False, help_text="YYYY-MM for staff_due; default this month.")
    ordering = serializers.ChoiceField(
        choices=["name", "-name", "balance", "-balance", "-created_at", "-last_entry_on"], required=False
    )


class ProductFilterSerializer(serializers.Serializer):
    q = serializers.CharField(required=False, max_length=100)
    low = serializers.BooleanField(required=False, allow_null=True, default=None)
    unit = serializers.ChoiceField(choices=Unit.choices, required=False)
    ordering = serializers.ChoiceField(choices=["name", "-name", "stock_level", "-stock_level"], required=False)


class StaffMonthSerializer(serializers.Serializer):
    month = serializers.CharField()
    days_present = serializers.IntegerField()
    days_in_month = serializers.IntegerField()
    earned = serializers.DecimalField(max_digits=14, decimal_places=2)
    advances = serializers.DecimalField(max_digits=14, decimal_places=2)
    salary_paid = serializers.DecimalField(max_digits=14, decimal_places=2)
    due = serializers.DecimalField(max_digits=14, decimal_places=2)
    monthly_salary = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    present_days = serializers.ListField(child=serializers.DateField())


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, style={"input_type": "password"})


class ShopSerializer(BaseSerializer):
    class Meta:
        model = Shop
        fields = ("name", "preferred_language")
        read_only_fields = ("name",)

    def validate_preferred_language(self, value: str) -> str:
        if value not in SHOP_LANGUAGES:
            raise serializers.ValidationError(f"Choose one of: {', '.join(SHOP_LANGUAGES)}.")
        return value


class MeSerializer(serializers.Serializer):
    username = serializers.CharField(read_only=True)
    shop = ShopSerializer()


class SessionSerializer(MeSerializer):
    token = serializers.CharField(read_only=True)


class RegisterSerializer(serializers.Serializer):
    "A new owner and their shop. The password follows Django's password rules."

    shop_name = serializers.CharField(max_length=200)
    username = serializers.RegexField(r"^[\w.@+-]+$", max_length=150)
    password = serializers.CharField(max_length=128, style={"input_type": "password"})
    preferred_language = serializers.ChoiceField(choices=SHOP_LANGUAGES, default="en-IN")

    def validate_shop_name(self, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError("Enter the shop's name.")
        return value

    def validate_username(self, value: str) -> str:
        from django.contrib.auth import get_user_model

        if get_user_model().objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("That username is taken.")
        return value

    def validate(self, data: dict) -> dict:
        from django.contrib.auth import get_user_model
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError

        try:
            validate_password(data["password"], get_user_model()(username=data["username"]))
        except ValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return data
