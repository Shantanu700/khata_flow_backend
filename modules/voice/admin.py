from django.contrib import admin

from modules.voice.models import VoiceCommand


@admin.register(VoiceCommand)
class VoiceCommandAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "input_mode", "stage", "transcript", "error_code", "shop")
    list_filter = ("stage", "input_mode", "language", "shop")
    search_fields = ("transcript", "response_text")
    readonly_fields = ("timings_ms", "parsed", "clarification")
