import json
from functools import cache
from pathlib import Path

from django.conf import settings

from modules.shops.choices import SHOP_LANGUAGES

DIR = Path(__file__).parent / "catalogs"
# hi-Latn is the source: every other catalog has exactly its keys.
BASE = "hi-Latn"

NAMES = {
    "hi-Latn": ("Hinglish", "Hinglish"),
    "hi-IN": ("Hindi", "हिन्दी"),
    "en-IN": ("English", "English"),
    "bn-IN": ("Bengali", "বাংলা"),
    "gu-IN": ("Gujarati", "ગુજરાતી"),
    "kn-IN": ("Kannada", "ಕನ್ನಡ"),
    "ml-IN": ("Malayalam", "മലയാളം"),
    "mr-IN": ("Marathi", "मराठी"),
    "pa-IN": ("Punjabi", "ਪੰਜਾਬੀ"),
    "ta-IN": ("Tamil", "தமிழ்"),
    "te-IN": ("Telugu", "తెలుగు"),
}


def catalog(code: str) -> dict[str, str]:
    "The screens' words for a language; keys missing from a translation fall back to Hinglish."
    # Read fresh while developing, so edited catalogs show without a restart.
    return _read(code) if settings.DEBUG else _cached(code)


@cache
def _cached(code: str) -> dict[str, str]:
    return _read(code)


def _read(code: str) -> dict[str, str]:
    base = json.loads((DIR / f"{BASE}.json").read_text("utf-8"))
    if code == BASE:
        return base
    path = DIR / f"{code}.json"
    return {**base, **json.loads(path.read_text("utf-8"))} if path.exists() else base


def languages() -> list[dict]:
    return [{"code": code, "name": NAMES[code][0], "native_name": NAMES[code][1]} for code in SHOP_LANGUAGES]
