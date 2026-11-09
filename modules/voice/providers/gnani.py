import json
import time
from collections.abc import Callable

import httpx
from django.conf import settings
from pydantic import BaseModel, ValidationError

from modules.voice.providers.base import ProviderError, Transcript

TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
EXTENSIONS = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3", "audio/ogg": "ogg", "audio/flac": "flac"}
MAX_KEYTERMS = 200


class PrismaResponse(BaseModel):
    success: bool
    transcript: str
    model: str | None = None
    detected_language_code: str | None = None
    truncated: bool = False


def _error_type(response: httpx.Response) -> str:
    "Gnani has several error shapes: {error: {type}}, {detail: {error_code}}, {detail: [...]}, {message}."
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        error, detail = body.get("error"), body.get("detail")
        if isinstance(error, dict):
            return str(error.get("type", ""))
        if isinstance(detail, dict):
            return str(detail.get("error_code", ""))
        return str(body.get("code") or body.get("message") or "")[:200]
    return ""


def _send(service: str, request: Callable[[httpx.Client], httpx.Response]) -> httpx.Response:
    if not settings.GNANI_API_KEY:
        raise ProviderError(f"GNANI_{service}_NOT_CONFIGURED", "GNANI_API_KEY is empty")
    for attempt in (1, 2):
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                response = request(client)
        except httpx.TimeoutException as exc:
            if attempt == 2:
                raise ProviderError(f"GNANI_{service}_TIMEOUT", type(exc).__name__) from exc
            continue
        except httpx.HTTPError as exc:
            raise ProviderError(f"GNANI_{service}_NETWORK", type(exc).__name__) from exc
        # 429 here usually means "one active request at a time" on the account, so a short wait clears it.
        if attempt == 1 and (response.status_code >= 500 or response.status_code == 429):
            time.sleep(0.5)
            continue
        break
    if response.status_code != 200:
        raise ProviderError(f"GNANI_{service}_HTTP_{response.status_code}", _error_type(response))
    return response


class GnaniPrismaSTT:
    def transcribe(
        self, audio: bytes, mime_type: str, language: str, keyterms: list[str] | None = None
    ) -> Transcript:
        data = {"model": settings.GNANI_STT_MODEL, "language_code": language, "format": settings.GNANI_STT_FORMAT}
        if keyterms:
            data["keyterms"] = json.dumps(keyterms[:MAX_KEYTERMS], ensure_ascii=False)
        # Prisma detects the audio format from the file-name extension.
        filename = f"clip.{EXTENSIONS.get(mime_type, 'wav')}"
        response = _send(
            "STT",
            lambda client: client.post(
                f"{settings.GNANI_BASE_URL}/stt/v3",
                headers={"X-API-Key-ID": settings.GNANI_API_KEY},
                files={"audio_file": (filename, audio, mime_type)},
                data=data,
            ),
        )
        try:
            body = PrismaResponse.model_validate_json(response.content)
        except ValidationError as exc:
            raise ProviderError("GNANI_STT_BAD_RESPONSE", str(exc.errors()[0]["loc"])) from exc
        if not body.success:
            raise ProviderError("GNANI_STT_FAILED")
        return Transcript(text=body.transcript.strip(), language=body.detected_language_code or language)


class GnaniTimbreTTS:
    def synthesize(self, text: str, language: str) -> bytes:
        payload = {
            "text": text,
            "voice": settings.GNANI_TTS_VOICE,
            "model": settings.GNANI_TTS_MODEL,
            "language": language,
            "speed": 1.0,
            "audio_config": {
                "sample_rate": 24000,
                "num_channels": 1,
                "sample_width": 2,
                "encoding": "linear_pcm",
                "container": "wav",
            },
        }
        response = _send(
            "TTS",
            lambda client: client.post(
                f"{settings.GNANI_BASE_URL}/api/v1/tts/inference",
                headers={"X-API-Key-ID": settings.GNANI_API_KEY},
                json=payload,
            ),
        )
        if not response.content.startswith(b"RIFF"):
            raise ProviderError("GNANI_TTS_BAD_AUDIO", response.headers.get("content-type", ""))
        return response.content
