import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, TypeVar

from django.conf import settings
from django.db.models import Model
from rapidfuzz import fuzz

from modules.shops.choices import PartyKind
from modules.shops.models import Party, Product, Shop

HONORIFICS = {"ji", "jee", "aunty", "auntie", "uncle", "bhaiya", "bhai", "didi", "sahab", "saab", "seth"}

Row = TypeVar("Row", bound=Model)


class Outcome(StrEnum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"


@dataclass
class Resolution(Generic[Row]):
    outcome: Outcome
    match: Row | None = None
    options: list[Row] = field(default_factory=list)


def normalise(name: str) -> str:
    words = re.sub(r"[^\w\s]", " ", name.lower()).split()
    return " ".join(word for word in words if word not in HONORIFICS) or " ".join(words)


def _score(spoken: str, row: Model) -> float:
    names = [row.name, *(row.aliases or [])]
    return max(fuzz.WRatio(spoken, normalise(candidate)) for candidate in names)


def resolve(name: str, rows: list[Row]) -> Resolution[Row]:
    spoken = normalise(name)
    ranked = sorted(((_score(spoken, row), row) for row in rows), key=lambda pair: pair[0], reverse=True)
    if not ranked or ranked[0][0] < settings.RESOLVER_MIN_SCORE:
        return Resolution(Outcome.UNKNOWN)
    top_score, top = ranked[0]
    close = [row for score, row in ranked[1:2] if top_score - score <= settings.RESOLVER_MARGIN]
    if top_score >= settings.RESOLVER_AUTO_SCORE and not close:
        return Resolution(Outcome.RESOLVED, match=top)
    return Resolution(Outcome.AMBIGUOUS, options=[top, *close])


def resolve_party(shop: Shop, name: str, kind: PartyKind) -> Resolution[Party]:
    return resolve(name, list(shop.parties.filter(kind=kind, is_active=True)))


def resolve_product(shop: Shop, name: str) -> Resolution[Product]:
    return resolve(name, list(shop.products.filter(is_active=True)))
