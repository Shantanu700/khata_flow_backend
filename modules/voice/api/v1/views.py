import json
import logging
import queue
import threading
from collections.abc import Callable

from django.conf import settings
from django.db import connection
from django.db.models import Count, OuterRef, Subquery
from django.http import FileResponse, Http404, StreamingHttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import get_object_or_404
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.utils.encoders import JSONEncoder

from base.decorators import extend_response
from base.exceptions import DomainError
from base.pagination import DefaultPagination
from base.views import BaseAV
from modules.shops.services import shop_for
from modules.voice import pipeline, stream
from modules.voice.api.v1.serializers import (
    AudioCommandSerializer,
    ChatSerializer,
    ClarifySerializer,
    StreamTicketSerializer,
    TextCommandSerializer,
    VoiceCommandSerializer,
)
from modules.voice.choices import InputMode
from modules.voice.models import Chat, VoiceCommand
from modules.voice.pipeline import cancel, clarify, handle_command

log = logging.getLogger(__name__)


# ?progress=1 streams NDJSON instead of one JSON body (see _respond).
PROGRESS = OpenApiParameter("progress", bool)


def _body(request, command: VoiceCommand) -> dict:
    return VoiceCommandSerializer(command, context={"request": request}).data


def _chat(shop, chat_id: int | None) -> Chat:
    "The chat a message goes into; a new one when the screen has none open."
    if chat_id:
        return get_object_or_404(Chat, pk=chat_id, shop=shop)
    return Chat.objects.create(shop=shop, last_message_at=timezone.now())


def _respond(request, command: VoiceCommand, run: Callable[[], VoiceCommand]):
    """The command as JSON; with ?progress=1, NDJSON instead: {"id"} first (so the screen can cancel it), then
    {"stage", "ms"} as each pipeline stage finishes, then {"command": …} (or {"error": {"code", "message"}})."""
    if not request.query_params.get("progress"):
        return _body(request, run())
    events: queue.Queue = queue.Queue()
    events.put({"id": command.pk})

    def work() -> None:
        reset = pipeline.progress.set(lambda stage, ms: events.put({"stage": stage, "ms": ms}))
        try:
            events.put({"command": _body(request, run())})
        except DomainError as exc:
            events.put({"error": {"code": exc.code, "message": exc.message}})
        except Exception:
            log.exception("streamed voice command failed")
            events.put({"error": {"code": "SERVER_ERROR", "message": "Something went wrong."}})
        finally:
            pipeline.progress.reset(reset)
            connection.close()
            events.put(None)

    threading.Thread(target=work, daemon=True).start()
    # DRF's encoder, as for every other response: entries carry Decimal amounts.
    lines = (json.dumps(event, cls=JSONEncoder, ensure_ascii=False) + "\n" for event in iter(events.get, None))
    return StreamingHttpResponse(lines, content_type="application/x-ndjson")


class AudioCommandAV(BaseAV):
    "GET: the shop's commands, newest first. POST: speak a command (synchronous; clips are at most 20 seconds)."

    parser_classes = [MultiPartParser, FormParser]

    @extend_response(VoiceCommandSerializer(), paginated=True)
    def get(self, request):
        commands = VoiceCommand.objects.filter(shop=shop_for(request.user)).order_by("-id")
        pages = DefaultPagination()
        page = pages.paginate_queryset(commands, request, view=self)
        return pages.get_paginated_response([_body(request, c) for c in page])

    @extend_schema(request=AudioCommandSerializer, parameters=[PROGRESS])
    @extend_response(VoiceCommandSerializer())
    def post(self, request):
        shop = shop_for(request.user)
        data = AudioCommandSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        command = VoiceCommand.objects.create(
            shop=shop,
            chat=_chat(shop, data.validated_data.get("chat")),
            input_mode=InputMode.AUDIO,
            audio=data.validated_data["audio"],
            language=data.validated_data.get("language", shop.preferred_language),
        )
        return _respond(request, command, lambda: handle_command(command))


class TextCommandAV(BaseAV):
    "Type a command: the same pipeline without speech-to-text. The demo backup."

    @extend_schema(request=TextCommandSerializer, parameters=[PROGRESS])
    @extend_response(VoiceCommandSerializer())
    def post(self, request):
        shop = shop_for(request.user)
        data = TextCommandSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        command = VoiceCommand.objects.create(
            shop=shop,
            chat=_chat(shop, data.validated_data.get("chat")),
            input_mode=InputMode.TEXT,
            transcript=data.validated_data["text"].strip(),
            language=data.validated_data.get("language", shop.preferred_language),
        )
        return _respond(request, command, lambda: handle_command(command))


class ChatListAV(BaseAV):
    "The shop's chats, most recently active first."

    @extend_response(ChatSerializer(), paginated=True)
    def get(self, request):
        last = VoiceCommand.objects.filter(chat=OuterRef("pk")).order_by("-id")
        chats = (
            Chat.objects.filter(shop=shop_for(request.user))
            .annotate(
                message_count=Count("messages"),
                last_stage=Subquery(last.values("stage")[:1]),
                last_reply=Subquery(last.values("response_text")[:1]),
            )
            .order_by("-last_message_at", "-id")
        )
        pages = DefaultPagination()
        page = pages.paginate_queryset(chats, request, view=self)
        return pages.get_paginated_response(ChatSerializer(page, many=True).data)


class ChatMessagesAV(BaseAV):
    "One chat's messages, newest first (each is the shopkeeper's words and Khata's reply)."

    @extend_response(VoiceCommandSerializer(), paginated=True)
    def get(self, request, pk: int):
        chat = get_object_or_404(Chat, pk=pk, shop=shop_for(request.user))
        pages = DefaultPagination()
        page = pages.paginate_queryset(chat.messages.order_by("-id"), request, view=self)
        return pages.get_paginated_response([_body(request, c) for c in page])


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
        answer = {"option_id": data.validated_data.get("option_id"), "confirm": data.validated_data.get("confirm")}
        return _respond(request, command, lambda: clarify(command, **answer))


class CancelAV(BaseAV):
    "Cancel a command while it runs; an entry it already wrote is reversed."

    @extend_schema(request=None)
    @extend_response(VoiceCommandSerializer())
    def post(self, request, pk: int):
        return _body(request, cancel(get_object_or_404(VoiceCommand, pk=pk, shop=shop_for(request.user))))


class StreamTicketAV(BaseAV):
    "A 30-second ticket for the live-transcript WebSocket, which cannot take the API token."

    @extend_schema(request=None, responses=StreamTicketSerializer)
    def post(self, request):
        return Response({"ticket": stream.ticket(shop_for(request.user)), "url": settings.VOICE_STREAM_URL})


class ReplyAudioAV(BaseAV):
    "The spoken reply (WAV), only to the shop's owner. Recordings are never served."

    @extend_schema(responses={(200, "audio/wav"): OpenApiTypes.BINARY})
    def get(self, request, pk: int):
        command = get_object_or_404(VoiceCommand, pk=pk, shop=shop_for(request.user))
        if not command.response_audio:
            raise Http404
        response = FileResponse(command.response_audio.open("rb"), content_type="audio/wav")
        response["Cache-Control"] = "private, max-age=3600"
        return response
