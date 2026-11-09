from django.db import models
from django.db.models import Q
from django.utils import timezone

from base.model import ModelBase
from modules.ledger.choices import CUSTOMER_MONEY_KINDS, STAFF_MONEY_KINDS, STOCK_KINDS, EntryKind
from modules.shops.models import Party, Product, Shop


class Entry(ModelBase):
    "Append-only: corrections are REVERSAL rows, never updates or deletes (status stays CREATED)."

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="entries")
    kind = models.CharField(max_length=24, choices=EntryKind.choices)
    party = models.ForeignKey(Party, on_delete=models.PROTECT, null=True, blank=True, related_name="entries")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True, related_name="entries")
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    occurred_on = models.DateField(default=timezone.localdate)
    # One-to-one: an entry can be reversed at most once.
    reverses = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversal"
    )
    voice_command = models.ForeignKey(
        "voice.VoiceCommand", on_delete=models.SET_NULL, null=True, blank=True, related_name="entries"
    )
    note = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        verbose_name_plural = "entries"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["shop", "kind", "occurred_on"], name="idx_entry_shop_kind_date"),
            models.Index(fields=["shop", "party"], name="idx_entry_shop_party"),
        ]
        constraints = [
            models.CheckConstraint(
                name="entry_fields_match_kind",
                condition=(
                    Q(kind=EntryKind.REVERSAL, reverses__isnull=False)
                    | Q(reverses__isnull=True)
                    & (
                        Q(kind__in=CUSTOMER_MONEY_KINDS + STAFF_MONEY_KINDS, party__isnull=False, amount__gt=0)
                        | Q(kind__in=STOCK_KINDS, product__isnull=False, quantity__gt=0)
                        | Q(kind=EntryKind.ATTENDANCE, party__isnull=False)
                    )
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} #{self.pk}"
