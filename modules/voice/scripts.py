"""The language a typed transcript is written in, from its script.

Prisma reports the language for speech; typed text has no such tag, so it is judged the same way here. Latin
script is left unjudged: English and Hinglish are written the same way.
"""

import unicodedata

# Devanagari is shared by Hindi and Marathi; the most a script can say is "one of those".
SCRIPTS = (
    ("hi-IN", "\u0900", "\u097f"),
    ("bn-IN", "\u0980", "\u09ff"),
    ("pa-IN", "\u0a00", "\u0a7f"),
    ("gu-IN", "\u0a80", "\u0aff"),
    ("ta-IN", "\u0b80", "\u0bff"),
    ("te-IN", "\u0c00", "\u0c7f"),
    ("kn-IN", "\u0c80", "\u0cff"),
    ("ml-IN", "\u0d00", "\u0d7f"),
)
# Fewer letters than this (a number, a name) is not a language; a stray glyph should not start a suggestion.
# Letters only: vowel signs and marks are characters too, so "हाँ" (one letter) would otherwise count as three.
MIN_LETTERS = 3


def script_language(text: str) -> str:
    "The language whose script `text` mostly uses, or '' when it is Latin or has too few letters."
    letters = [char for char in text if unicodedata.category(char).startswith("L")]
    counts = {code: sum(start <= char <= end for char in letters) for code, start, end in SCRIPTS}
    code, count = max(counts.items(), key=lambda pair: pair[1])
    return code if count >= MIN_LETTERS else ""
