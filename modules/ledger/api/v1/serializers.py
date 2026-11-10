from rest_framework import serializers

from base.model import ModelBase
from base.serializers import BaseSerializer
from modules.ledger.choices import EntryKind
from modules.ledger.models import Entry


class EntrySerializer(BaseSerializer):
    dynamic_keys = ["party.name", "product.name", "product.unit", "voice_command.input_mode"]
    is_reversed = serializers.SerializerMethodField()
    # Customer balance after this entry, when the list is filtered to one customer; null on undone rows.
    running_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, allow_null=True, default=None)

    class Meta:
        model = Entry
        fields = ModelBase.BASE_MODEL_FIELDS + (
            "kind", "party", "product", "amount", "quantity", "occurred_on", "reverses", "is_reversed",
            "voice_command", "note", "running_balance",
        )
        read_only_fields = fields

    def get_is_reversed(self, entry: Entry) -> bool:
        return hasattr(entry, "reversal")


ENTRY_ORDERING = ["-id", "id", "amount", "-amount", "quantity", "-quantity", "party__name", "product__name"]


class EntryFilterSerializer(serializers.Serializer):
    kind = serializers.CharField(required=False, help_text="One kind or several, comma-separated.")
    party = serializers.IntegerField(required=False)
    product = serializers.IntegerField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    q = serializers.CharField(required=False, max_length=100, help_text="Party or product name, note, or #id.")
    ordering = serializers.ChoiceField(choices=ENTRY_ORDERING, required=False)

    def validate_kind(self, value: str) -> list[str]:
        kinds = [k.strip() for k in value.split(",") if k.strip()]
        unknown = [k for k in kinds if k not in EntryKind.values]
        if unknown:
            raise serializers.ValidationError(f"Unknown kind: {', '.join(unknown)}.")
        return kinds


class EntryCreateSerializer(serializers.Serializer):
    "A ledger entry typed in the app. Corrections are undo (a REVERSAL), never edits."

    kind = serializers.ChoiceField(choices=[k for k in EntryKind.choices if k[0] != EntryKind.REVERSAL])
    party = serializers.IntegerField(required=False, allow_null=True)
    product = serializers.IntegerField(required=False, allow_null=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, min_value=0)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, required=False, allow_null=True, min_value=0)
    occurred_on = serializers.DateField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class DebtorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    balance = serializers.DecimalField(max_digits=14, decimal_places=2)
    photo_key = serializers.CharField(allow_null=True)


class LowStockSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    unit = serializers.CharField()
    stock_level = serializers.DecimalField(max_digits=14, decimal_places=3)
    threshold = serializers.DecimalField(max_digits=14, decimal_places=3)


class StaffDueSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    days_present = serializers.IntegerField()
    days_in_month = serializers.IntegerField()
    earned = serializers.DecimalField(max_digits=14, decimal_places=2)
    advances = serializers.DecimalField(max_digits=14, decimal_places=2)
    salary_paid = serializers.DecimalField(max_digits=14, decimal_places=2)
    due = serializers.DecimalField(max_digits=14, decimal_places=2)


class DaySerializer(serializers.Serializer):
    date = serializers.DateField()
    credit = serializers.DecimalField(max_digits=14, decimal_places=2)
    payment = serializers.DecimalField(max_digits=14, decimal_places=2)


class DueAgeSerializer(serializers.Serializer):
    max_days = serializers.IntegerField(allow_null=True, help_text="Days since the customer's last udhaar or payment, up to this; null means older.")
    count = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)


class SummarySerializer(serializers.Serializer):
    total_outstanding = serializers.DecimalField(max_digits=14, decimal_places=2)
    dues_by_age = DueAgeSerializer(many=True, help_text="What customers owe, by how long their khata has been quiet.")
    owing_count = serializers.IntegerField()
    customer_count = serializers.IntegerField()
    top_debtors = DebtorSerializer(many=True)
    low_stock = LowStockSerializer(many=True)
    staff_dues = StaffDueSerializer(many=True)
    today_entry_count = serializers.IntegerField()
    daily = DaySerializer(many=True, help_text="The last 14 days, oldest first.")
    week_credit = serializers.DecimalField(max_digits=14, decimal_places=2)
    week_payment = serializers.DecimalField(max_digits=14, decimal_places=2)
    entry_count_14d = serializers.IntegerField()
    recent = EntrySerializer(many=True, help_text="The 6 latest money entries (no stock movements or attendance).")
