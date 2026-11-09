import pytest

from modules.shops.choices import PartyKind
from modules.shops.models import Party
from modules.voice.resolver import Outcome, normalise, resolve_party, resolve_product


@pytest.fixture
def customers(shop, sharma, verma):
    Party.objects.create(shop=shop, name="Gupta aunty", aliases=["gupta", "gupta ji"])
    return shop


def test_normalise_strips_honorifics_and_punctuation():
    assert normalise("Sharma Ji!") == "sharma"
    assert normalise("Gupta  Aunty") == "gupta"
    assert normalise("ji") == "ji"


@pytest.mark.parametrize("spoken", ["Sharma ji", "sharma", "Sharma uncle", "SHARMA JI", "sharmaa"])
def test_exact_alias_honorific_and_typo_resolve(customers, sharma, spoken):
    result = resolve_party(customers, spoken, PartyKind.CUSTOMER)
    assert result.outcome == Outcome.RESOLVED
    assert result.match == sharma


def test_close_names_ask_for_clarification(customers, sharma, verma):
    result = resolve_party(customers, "Varma ji", PartyKind.CUSTOMER)
    assert result.outcome == Outcome.AMBIGUOUS
    assert result.options == [verma, sharma]


def test_unknown_name(customers):
    assert resolve_party(customers, "Pandey ji", PartyKind.CUSTOMER).outcome == Outcome.UNKNOWN


def test_wrong_kind_is_not_matched(customers, ramu):
    assert resolve_party(customers, "Ramu", PartyKind.CUSTOMER).outcome == Outcome.UNKNOWN
    assert resolve_party(customers, "Ramu", PartyKind.STAFF).match == ramu


def test_product_alias_and_partial_name(shop, aata, maggi):
    assert resolve_product(shop, "aata").match == aata
    assert resolve_product(shop, "maggie").match == maggi
    assert resolve_product(shop, "Fortune tel").outcome == Outcome.UNKNOWN


def test_inactive_rows_are_ignored(customers, sharma):
    sharma.is_active = False
    sharma.save()
    assert resolve_party(customers, "Sharma ji", PartyKind.CUSTOMER).outcome == Outcome.UNKNOWN
