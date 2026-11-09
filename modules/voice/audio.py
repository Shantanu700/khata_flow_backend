import subprocess
import tempfile
import wave
from pathlib import Path

from django.conf import settings

from modules.voice.providers.base import ProviderError

SAMPLE_RATE = 16000
MIN_SECONDS = 0.3


def normalise(audio: bytes) -> tuple[bytes, float]:
    "Any browser recording (webm/opus, mp4, wav...) to 16 kHz mono 16-bit WAV, plus its duration in seconds."
    if not audio:
        raise ProviderError("AUDIO_EMPTY")
    # Real files, not pipes: mp4/m4a need a seekable input, and WAV written to a pipe gets a bogus length header.
    with tempfile.TemporaryDirectory() as tmp:
        source, target = Path(tmp) / "input", Path(tmp) / "output.wav"
        source.write_bytes(audio)
        try:
            subprocess.run(
                ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(source),
                 "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16", str(target)],
                check=True,
                capture_output=True,
                timeout=20,
            )
        except FileNotFoundError as exc:
            raise ProviderError("FFMPEG_MISSING") from exc
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            stderr = getattr(exc, "stderr", b"") or b""
            raise ProviderError("AUDIO_UNREADABLE", stderr.decode(errors="replace")[-200:]) from exc
        wav_bytes = target.read_bytes()
        with wave.open(str(target)) as wav:
            seconds = wav.getnframes() / wav.getframerate()
    if seconds < MIN_SECONDS:
        raise ProviderError("AUDIO_TOO_SHORT", f"{seconds:.2f}s")
    if seconds > settings.MAX_AUDIO_SECONDS:
        raise ProviderError("AUDIO_TOO_LONG", f"{seconds:.1f}s")
    return wav_bytes, seconds
