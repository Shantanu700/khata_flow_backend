from django.db import models

from base.model import ModelBase
from modules.shops.models import Shop
from modules.voice.choices import CommandStatus, InputMode


class Chat(ModelBase):
    "A conversation on the Speak screen: the shopkeeper's messages and Khata's replies, each a VoiceCommand."

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="chats")
    title = models.CharField(max_length=120, blank=True, default="")
    last_message_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["-last_message_at", "-id"]

    def __str__(self) -> str:
        return f"Chat #{self.pk} {self.title}"


class VoiceCommand(ModelBase):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="voice_commands")
    # Null only for commands made before chats, until group_old_commands files them.
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, null=True, blank=True, related_name="messages")
    audio = models.FileField(upload_to="commands/%Y/%m/", null=True, blank=True)
    input_mode = models.CharField(max_length=8, choices=InputMode.choices)
    language = models.CharField(max_length=16)
    transcript = models.TextField(blank=True, default="")
    # Prisma's REST response carries no confidence score, so this stays null for Gnani.
    stt_confidence = models.FloatField(null=True, blank=True)
    # What the words actually came in: Prisma's tag for speech, the transcript's script for typed text.
    # Empty when it cannot be told (Latin script: English and Hinglish look alike).
    detected_language = models.CharField(max_length=16, blank=True, default="")
    parsed = models.JSONField(null=True, blank=True)
    # Pipeline progress; `status` (from ModelBase) is the row's lifecycle like every other model.
    stage = models.CharField(max_length=24, choices=CommandStatus.choices, default=CommandStatus.RECEIVED)
    # {"question": str, "kind": "choose" | "create_party" | "confirm_amount" | "confirm_create" | "fill", "options": [{"id", "label"}],
    #  "answers": {"create_confirmed": True, …}, "expires_at": iso}
    clarification = models.JSONField(null=True, blank=True)
    response_text = models.TextField(blank=True, default="")
    response_audio = models.FileField(upload_to="responses/%Y/%m/", null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_detail = models.TextField(blank=True, default="")
    timings_ms = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"{self.input_mode} #{self.pk} {self.stage}"
