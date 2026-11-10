from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from modules.shops.choices import PartyKind, Unit
from modules.shops.models import Party, Product, Shop


@pytest.fixture
def shop(db) -> Shop:
    owner = get_user_model().objects.create_user(username="owner", password="test-pass-123")
    return Shop.objects.create(owner=owner, name="Test Kirana", preferred_language="hi-Latn")


@pytest.fixture
def sharma(shop) -> Party:
    return Party.objects.create(shop=shop, name="Sharma ji", aliases=["sharma", "sharma uncle"])


@pytest.fixture
def verma(shop) -> Party:
    return Party.objects.create(shop=shop, name="Verma ji", aliases=["verma"])


@pytest.fixture
def ramu(shop) -> Party:
    return Party.objects.create(shop=shop, name="Ramu", kind=PartyKind.STAFF, monthly_salary=Decimal("9000"))


@pytest.fixture
def aata(shop) -> Product:
    return Product.objects.create(
        shop=shop, name="Aashirvaad Aata 5kg", aliases=["aata", "atta"], unit=Unit.PACKET, low_stock_threshold=5
    )


@pytest.fixture
def maggi(shop) -> Product:
    return Product.objects.create(shop=shop, name="Maggi", aliases=["maggie"], low_stock_threshold=10)
