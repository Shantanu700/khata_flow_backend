from django.db import models

from base.model import ModelBase
from modules.shops.models import Shop
from modules.voice.choices import CommandStatus, InputMode


class VoiceCommand(ModelBase):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="voice_commands")
    audio = models.FileField(upload_to="commands/%Y/%m/", null=True, blank=True)
    input_mode = models.CharField(max_length=8, choices=InputMode.choices)
    language = models.CharField(max_length=16)
    transcript = models.TextField(blank=True, default="")
    # Prisma's REST response carries no confidence score, so this stays null for Gnani.
    stt_confidence = models.FloatField(null=True, blank=True)
    parsed = models.JSONField(null=True, blank=True)
    # Pipeline progress; `status` (from ModelBase) is the row's lifecycle like every other model.
    stage = models.CharField(max_length=24, choices=CommandStatus.choices, default=CommandStatus.RECEIVED)
    # {"question": str, "kind": "choose" | "create_party" | "confirm_amount", "options": [{"id", "label"}], "expires_at": iso}
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
