from django.conf import settings
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from base.model import ModelBase
from base.serializers import BaseSerializer
from modules.ledger.api.v1.serializers import EntrySerializer
from modules.shops.choices import SHOP_LANGUAGES
from modules.voice.models import VoiceCommand

# Prisma and Timbre's language codes (docs.gnani.ai/api/STT/languages), plus hi-Latn: Hindi voice, Hinglish replies.
LANGUAGES = SHOP_LANGUAGES


class AudioCommandSerializer(serializers.Serializer):
    audio = serializers.FileField()
    language = serializers.ChoiceField(choices=LANGUAGES, required=False)

    def validate_audio(self, audio):
        if audio.size > settings.MAX_UPLOAD_BYTES:
            raise serializers.ValidationError("Audio file is too large.")
        return audio


class StreamTicketSerializer(serializers.Serializer):
    ticket = serializers.CharField()
    url = serializers.CharField(help_text="The live-transcript WebSocket; connect with ?ticket=…&language=…")


class TextCommandSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=500)
    language = serializers.ChoiceField(choices=LANGUAGES, required=False)


class ClarifySerializer(serializers.Serializer):
    option_id = serializers.CharField(required=False, max_length=64)
    confirm = serializers.BooleanField(required=False, allow_null=True, default=None)

    def validate(self, data: dict) -> dict:
        if not data.get("option_id") and data.get("confirm") is None:
            raise serializers.ValidationError("Send option_id or confirm.")
        return data


class VoiceCommandSerializer(BaseSerializer):
    response_audio_url = serializers.SerializerMethodField()
    entry = serializers.SerializerMethodField()

    class Meta:
        model = VoiceCommand
        fields = ModelBase.BASE_MODEL_FIELDS + (
            "stage", "input_mode", "language", "transcript", "parsed", "response_text",
            "response_audio_url", "entry", "clarification", "error_code", "timings_ms",
        )
        read_only_fields = fields

    def get_response_audio_url(self, command: VoiceCommand) -> str | None:
        "The authenticated reply-audio endpoint (never a public media URL)."
        if not command.response_audio:
            return None
        request = self.context.get("request")
        url = f"/api/v1/voice/commands/{command.pk}/audio/"
        return request.build_absolute_uri(url) if request else url

    @extend_schema_field(EntrySerializer(allow_null=True))
    def get_entry(self, command: VoiceCommand) -> dict | None:
        entry = command.entries.select_related("party", "product").order_by("-id").first()
        return EntrySerializer(entry).data if entry else None
