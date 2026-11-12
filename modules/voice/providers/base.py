from typing import Protocol

from pydantic import BaseModel


class Transcript(BaseModel):
    text: str
    language: str  # the language Prisma heard; "" when it gave none
    confidence: float | None = None


class ProviderError(Exception):
    "A speech provider failed. `detail` is for logs and never contains credentials."

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class SpeechToText(Protocol):
    def transcribe(
        self, audio: bytes, mime_type: str, language: str, keyterms: list[str] | None = None
    ) -> Transcript: ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str, language: str) -> bytes: ...
