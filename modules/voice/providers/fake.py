import io
import wave

from modules.voice.providers.base import Transcript


def silent_wav(seconds: float = 0.2, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


class FakeSTT:
    "Returns `transcript` for any audio; tests set it per case."

    transcript = ""

    def transcribe(self, audio: bytes, mime_type: str, language: str, keyterms: list[str] | None = None) -> Transcript:
        return Transcript(text=self.transcript, language=language)


class FakeTTS:
    def synthesize(self, text: str, language: str) -> bytes:
        return silent_wav()
