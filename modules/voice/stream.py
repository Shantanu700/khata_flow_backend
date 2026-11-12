"""Live transcript while the shopkeeper speaks: relays the browser's microphone (16 kHz 16-bit mono PCM, 1,024-byte
frames) to Gnani's streaming speech-to-text and sends back each phrase as Prisma finishes it. Display only: the
command itself still goes through the pipeline when the recording is uploaded.

The browser cannot set Gnani's key header, and keeps no token in JavaScript, so it connects here with a short-lived
signed ticket from POST /api/v1/voice/stream-ticket/ (made through the app's proxy)."""

import asyncio
import json
from urllib.parse import parse_qs

import websockets
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core import signing
from django.db import connection

from modules.shops.choices import SHOP_LANGUAGES, voice_language
from modules.shops.models import Shop

SALT = "voice-stream"
TICKET_SECONDS = 30
FRAME_BYTES = 1024
# A command is at most 20 seconds of speech; anything open much longer is abandoned.
MAX_SESSION_SECONDS = 40
UNAUTHORISED, UPSTREAM_DOWN = 4401, 4502


def ticket(shop: Shop) -> str:
    return signing.dumps(shop.pk, salt=SALT)


def _keyterms(shop_id: int) -> list[str]:
    from modules.voice.pipeline import shop_keyterms

    try:
        return shop_keyterms(Shop.objects.get(pk=shop_id))
    finally:
        # Django closes connections after its own requests only; this handler runs outside them.
        connection.close()


async def stream(scope, receive, send) -> None:
    if (await receive())["type"] != "websocket.connect":
        return
    query = parse_qs(scope["query_string"].decode())
    try:
        shop_id = signing.loads(query.get("ticket", [""])[0], salt=SALT, max_age=TICKET_SECONDS)
    except signing.BadSignature:
        return await send({"type": "websocket.close", "code": UNAUTHORISED})
    language = query.get("language", [""])[0]
    terms = await sync_to_async(_keyterms)(shop_id)
    headers = {
        "x-api-key-id": settings.GNANI_API_KEY,
        # One language: the stream closes with 1008 on a list like "hi-IN,en-IN", which the recorded command takes.
        "language_code": voice_language(language if language in SHOP_LANGUAGES else settings.DEFAULT_LANGUAGE),
        "model": settings.GNANI_STT_MODEL,
        "format": settings.GNANI_STT_FORMAT,
        "x-sample-rate": "16000",
        # A phrase closes at a 200 ms pause, or after 2 s of unbroken speech, so text keeps appearing while the
        # shopkeeper talks (Prisma sends no partial results). A forced break can split a word in the live text;
        # the command itself is transcribed again in full.
        "x-min-silence-ms": "200",
        "x-max-speech-s": "2",
        "x-idle-timeout-sec": "30",
    }
    if terms:
        # ASCII-escaped JSON: header values cannot carry Devanagari or other scripts as-is.
        headers["keyterms"] = json.dumps(terms[:200])
    try:
        upstream = await websockets.connect(settings.GNANI_STREAM_URL, additional_headers=headers, open_timeout=5)
    except (OSError, websockets.WebSocketException, TimeoutError):
        return await send({"type": "websocket.close", "code": UPSTREAM_DOWN})
    await send({"type": "websocket.accept"})

    async def mic_to_gnani() -> None:
        while (message := await receive())["type"] != "websocket.disconnect":
            if len(message.get("bytes") or b"") == FRAME_BYTES:
                await upstream.send(message["bytes"])
            elif message.get("text") == "commit":
                await upstream.send(json.dumps({"type": "commit"}))

    async def gnani_to_browser() -> None:
        async for raw in upstream:
            event = json.loads(raw)
            if event.get("type") == "transcript" and event.get("text"):
                await send({"type": "websocket.send", "text": json.dumps({"text": event["text"]})})

    tasks = [asyncio.create_task(mic_to_gnani()), asyncio.create_task(gnani_to_browser())]
    try:
        await asyncio.wait(tasks, timeout=MAX_SESSION_SECONDS, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await upstream.close()
        try:
            await send({"type": "websocket.close", "code": 1000})
        except Exception:  # the browser already went away
            pass
