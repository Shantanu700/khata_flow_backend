from django.conf import settings

from modules.voice.providers.base import ProviderError, SpeechToText, TextToSpeech, Transcript
from modules.voice.providers.fake import FakeSTT, FakeTTS
from modules.voice.providers.gnani import GnaniPrismaSTT, GnaniTimbreTTS

__all__ = ["ProviderError", "SpeechToText", "TextToSpeech", "Transcript", "get_stt", "get_tts"]


def get_stt() -> SpeechToText:
    return {"gnani": GnaniPrismaSTT, "fake": FakeSTT}[settings.STT_PROVIDER]()


def get_tts() -> TextToSpeech:
    return {"gnani": GnaniTimbreTTS, "fake": FakeTTS}[settings.TTS_PROVIDER]()
