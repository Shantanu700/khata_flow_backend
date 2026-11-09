from drf_spectacular.utils import extend_schema
from rest_framework.generics import get_object_or_404
from rest_framework.parsers import FormParser, MultiPartParser

from base.decorators import extend_response
from base.views import BaseAV
from modules.shops.services import shop_for
from modules.voice.api.v1.serializers import (
    AudioCommandSerializer,
    ClarifySerializer,
    TextCommandSerializer,
    VoiceCommandSerializer,
)
from modules.voice.choices import InputMode
from modules.voice.models import VoiceCommand
from modules.voice.pipeline import clarify, handle_command


def _body(request, command: VoiceCommand) -> dict:
    return VoiceCommandSerializer(command, context={"request": request}).data


class AudioCommandAV(BaseAV):
    "Speak a command. Runs synchronously: clips are at most 20 seconds."

    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(request=AudioCommandSerializer)
    @extend_response(VoiceCommandSerializer())
    def post(self, request):
        shop = shop_for(request.user)
        data = AudioCommandSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        command = VoiceCommand.objects.create(
            shop=shop,
            input_mode=InputMode.AUDIO,
            audio=data.validated_data["audio"],
            language=data.validated_data.get("language", shop.preferred_language),
        )
        return _body(request, handle_command(command))


class TextCommandAV(BaseAV):
    "Type a command: the same pipeline without speech-to-text. The demo backup."

    @extend_schema(request=TextCommandSerializer)
    @extend_response(VoiceCommandSerializer())
    def post(self, request):
        shop = shop_for(request.user)
        data = TextCommandSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        command = VoiceCommand.objects.create(
            shop=shop,
            input_mode=InputMode.TEXT,
            transcript=data.validated_data["text"].strip(),
            language=data.validated_data.get("language", shop.preferred_language),
        )
        return _body(request, handle_command(command))


class CommandDetailAV(BaseAV):
    @extend_response(VoiceCommandSerializer())
    def get(self, request, pk: int):
        return _body(request, get_object_or_404(VoiceCommand, pk=pk, shop=shop_for(request.user)))


class ClarifyAV(BaseAV):
    "Answer the question a command is waiting on: an option id, or confirm true/false."

    @extend_schema(request=ClarifySerializer)
    @extend_response(VoiceCommandSerializer())
    def post(self, request, pk: int):
        command = get_object_or_404(VoiceCommand, pk=pk, shop=shop_for(request.user))
        data = ClarifySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        clarify(command, option_id=data.validated_data.get("option_id"), confirm=data.validated_data.get("confirm"))
        return _body(request, command)
