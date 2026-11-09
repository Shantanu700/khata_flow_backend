from django.db.models import TextChoices


class PartyKind(TextChoices):
    CUSTOMER = "CUSTOMER", "Customer"
    STAFF = "STAFF", "Staff"
    SUPPLIER = "SUPPLIER", "Supplier"


class Unit(TextChoices):
    PACKET = "PACKET", "Packet"
    KG = "KG", "Kg"
    LITRE = "LITRE", "Litre"
    PIECE = "PIECE", "Piece"


# Gnani Prisma (speech-to-text) and Timbre v2.5 (text-to-speech) language codes.
VOICE_LANGUAGES = ["bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "pa-IN", "ta-IN", "te-IN"]
# A shop's language: a voice language, or hi-Latn (Hinglish screens; Hindi voice).
SHOP_LANGUAGES = ["hi-Latn", *VOICE_LANGUAGES]


def voice_language(code: str) -> str:
    return "hi-IN" if code == "hi-Latn" else code
