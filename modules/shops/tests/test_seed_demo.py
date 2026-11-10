from decimal import Decimal

from django.core.management import call_command

from modules.ledger.services import low_stock_products, total_outstanding
from modules.shops.models import Shop


def test_seed_creates_two_separate_merchants_and_is_idempotent(db):
    call_command("seed_demo")
    call_command("seed_demo")

    gupta = Shop.objects.get(owner__username="gupta_store")
    patel = Shop.objects.get(owner__username="patel_store")
    assert (gupta.entries.count(), patel.entries.count()) == (44, 34)
    assert total_outstanding(gupta) == Decimal("2500")
    assert total_outstanding(patel) == Decimal("1850")
    assert [p.name for p in low_stock_products(patel)] == ["Amul Milk 500ml", "Basmati Rice 5kg", "Surf Excel 1kg"]
    assert not patel.parties.filter(name="Sharma ji").exists()


def test_seeded_accounts_sign_in_with_the_demo_password(db, monkeypatch):
    from django.contrib.auth import authenticate

    monkeypatch.setenv("DEMO_PASSWORD", "sample-pass-1")
    call_command("seed_demo")
    for username in ("gupta_store", "patel_store"):
        assert authenticate(username=username, password="sample-pass-1") is not None
