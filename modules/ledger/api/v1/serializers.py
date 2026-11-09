from rest_framework import serializers

from base.model import ModelBase
from base.serializers import BaseSerializer
from modules.ledger.choices import EntryKind
from modules.ledger.models import Entry


class EntrySerializer(BaseSerializer):
    dynamic_keys = ["party.name", "product.name"]
    is_reversed = serializers.SerializerMethodField()

    class Meta:
        model = Entry
        fields = ModelBase.BASE_MODEL_FIELDS + (
            "kind", "party", "product", "amount", "quantity", "occurred_on", "reverses", "is_reversed",
            "voice_command", "note",
        )
        read_only_fields = fields

    def get_is_reversed(self, entry: Entry) -> bool:
        return hasattr(entry, "reversal")


class EntryFilterSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=EntryKind.choices, required=False)
    party = serializers.IntegerField(required=False)
    product = serializers.IntegerField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)


class DebtorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    balance = serializers.DecimalField(max_digits=14, decimal_places=2)


class LowStockSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
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


class SummarySerializer(serializers.Serializer):
    total_outstanding = serializers.DecimalField(max_digits=14, decimal_places=2)
    top_debtors = DebtorSerializer(many=True)
    low_stock = LowStockSerializer(many=True)
    staff_dues = StaffDueSerializer(many=True)
    today_entry_count = serializers.IntegerField()
