from django.contrib import admin

from modules.ledger.models import Entry


@admin.register(Entry)
class EntryAdmin(admin.ModelAdmin):
    list_display = ("id", "occurred_on", "kind", "party", "product", "amount", "quantity", "reverses", "shop")
    list_filter = ("kind", "occurred_on", "shop")
    search_fields = ("party__name", "product__name", "note")
    raw_id_fields = ("reverses", "voice_command")

    # Append-only: the admin may add rows but never edit or delete them.
    def has_change_permission(self, request, obj=None) -> bool:
        return obj is None

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
