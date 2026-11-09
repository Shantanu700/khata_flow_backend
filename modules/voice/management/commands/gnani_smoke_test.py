import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from modules.voice.audio import normalise
from modules.voice.providers import ProviderError
from modules.voice.providers.gnani import GnaniPrismaSTT, GnaniTimbreTTS

SENTENCE = "Sharma ji ka paanch sau udhaar likh do."
RUPEE_VARIANTS = {
    "rupee_symbol": "Theek hai. Sharma ji ka ₹1,250 udhaar baaki hai.",
    "rupaye_word": "Theek hai. Sharma ji ka 1250 rupaye udhaar baaki hai.",
}
KEYTERMS = ["Sharma ji", "Verma ji", "Gupta aunty", "Mishra ji", "Ramu", "Shyam", "Maggi", "Parle-G"]


class Command(BaseCommand):
    help = "Call the real Gnani Prisma (STT) and Timbre (TTS) APIs once each and print timings."

    def add_arguments(self, parser):
        parser.add_argument("--audio", help="A recording to transcribe (any format ffmpeg reads). "
                            "Without it, Timbre's own output is transcribed back.")
        parser.add_argument("--language", default=settings.DEFAULT_LANGUAGE)

    def handle(self, *args, audio=None, language, **options):
        out = Path(settings.MEDIA_ROOT) / "smoke"
        out.mkdir(parents=True, exist_ok=True)
        tts, stt = GnaniTimbreTTS(), GnaniPrismaSTT()
        try:
            for name, text in {"sentence": SENTENCE, **RUPEE_VARIANTS}.items():
                start = time.perf_counter()
                wav = tts.synthesize(text, settings.GNANI_TTS_LANGUAGE)
                path = out / f"tts_{name}.wav"
                path.write_bytes(wav)
                self.stdout.write(f"Timbre {self._ms(start)} ms -> {path} ({len(wav)} bytes)")

            source = Path(audio).read_bytes() if audio else (out / "tts_sentence.wav").read_bytes()
            start = time.perf_counter()
            clip, seconds = normalise(source)
            self.stdout.write(f"normalise {self._ms(start)} ms ({seconds:.1f}s of audio)")
            start = time.perf_counter()
            transcript = stt.transcribe(clip, "audio/wav", language, keyterms=KEYTERMS)
            self.stdout.write(f"Prisma {self._ms(start)} ms -> {transcript.text!r} ({transcript.language})")
        except (ProviderError, OSError) as exc:
            raise CommandError(f"Smoke test failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS("Gnani smoke test passed. Listen to the WAVs above."))

    @staticmethod
    def _ms(start: float) -> int:
        return round((time.perf_counter() - start) * 1000)
