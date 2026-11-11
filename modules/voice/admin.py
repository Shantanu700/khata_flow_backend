from django.contrib import admin

from modules.voice.models import Chat, VoiceCommand


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ("id", "last_message_at", "title", "shop")
    list_filter = ("shop",)
    search_fields = ("title",)


@admin.register(VoiceCommand)
class VoiceCommandAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "input_mode", "stage", "transcript", "error_code", "chat", "shop")
    list_filter = ("stage", "input_mode", "language", "shop")
    search_fields = ("transcript", "response_text")
    readonly_fields = ("timings_ms", "parsed", "clarification")
    raw_id_fields = ("chat",)
