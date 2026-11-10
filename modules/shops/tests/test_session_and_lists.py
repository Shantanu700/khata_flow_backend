from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from modules.ledger.choices import EntryKind
from modules.ledger.services import record_entry
from modules.shops.choices import PartyKind
from modules.shops.models import Party, Product


@pytest.fixture
def api(shop) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=shop.owner).key}")
    return client


def names(response) -> list[str]:
    return [row["name"] for row in response.json()["results"]]


def test_login_returns_token_and_shop_and_logout_revokes_it(shop):
    client = APIClient()
    assert client.post("/api/v1/auth/login/", {"username": "owner", "password": "wrong"}).status_code == 401
    body = client.post("/api/v1/auth/login/", {"username": "owner", "password": "test-pass-123"}).json()
    assert body["shop"] == {"name": "Test Kirana", "preferred_language": "hi-Latn", "photo_key": None}
    client.credentials(HTTP_AUTHORIZATION=f"Token {body['token']}")
    assert client.get("/api/v1/me/").json()["username"] == "owner"
    assert client.post("/api/v1/auth/logout/").status_code == 201
    assert client.get("/api/v1/me/").status_code == 401


def test_language_is_changed_with_put_and_validated(api):
    ok = api.put("/api/v1/me/", {"shop": {"preferred_language": "ta-IN"}}, format="json")
    assert ok.json()["shop"]["preferred_language"] == "ta-IN"
    bad = api.put("/api/v1/me/", {"shop": {"preferred_language": "fr-FR"}}, format="json")
    assert bad.status_code == 400


@pytest.mark.parametrize("path", ["/api/v1/me/", "/api/v1/parties/{party}/", "/api/v1/products/{product}/"])
def test_patch_is_never_offered(api, sharma, maggi, path):
    assert api.patch(path.format(party=sharma.pk, product=maggi.pk), {}, format="json").status_code == 405


def test_customers_search_filter_and_order_on_the_server(api, shop, sharma, verma):
    Party.objects.create(shop=shop, name="Mishra ji", aliases=["mishra uncle"])
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=Decimal("500"))
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=verma, amount=Decimal("900"))

    assert names(api.get("/api/v1/parties/?kind=CUSTOMER&q=uncle")) == ["Mishra ji", "Sharma ji"]
    assert names(api.get("/api/v1/parties/?kind=CUSTOMER&balance=owing&ordering=-balance")) == ["Verma ji", "Sharma ji"]
    assert names(api.get("/api/v1/parties/?kind=CUSTOMER&balance=clear")) == ["Mishra ji"]
    page = api.get("/api/v1/parties/?kind=CUSTOMER&page_size=2&page=2").json()
    assert (page["count"], len(page["results"])) == (3, 1)
    assert api.get("/api/v1/parties/?ordering=bogus").status_code == 400


def test_staff_advance_filter_attendance_and_month(api, shop, ramu):
    today = timezone.localdate()
    Party.objects.create(shop=shop, name="Shyam", kind=PartyKind.STAFF, monthly_salary=Decimal("8000"))
    record_entry(shop, EntryKind.STAFF_ADVANCE, party=ramu, amount=Decimal("1000"))
    marked = record_entry(shop, EntryKind.ATTENDANCE, party=ramu)

    assert names(api.get("/api/v1/parties/?kind=STAFF&advance=yes")) == ["Ramu"]
    assert names(api.get("/api/v1/parties/?kind=STAFF&advance=no")) == ["Shyam"]
    row = api.get(f"/api/v1/parties/{ramu.pk}/").json()
    assert row["present_today"] == marked.pk and row["attendance_14d"] == [today.isoformat()]

    month = api.get(f"/api/v1/parties/{ramu.pk}/month/?month={today:%Y-%m}").json()
    assert (month["days_present"], month["advances"], month["present_days"]) == (1, 1000, [today.isoformat()])
    earlier = today.replace(day=1) - timedelta(days=1)
    assert api.get(f"/api/v1/parties/{ramu.pk}/month/?month={earlier:%Y-%m}").json()["present_days"] == []


def test_products_search_low_and_order(api, shop, aata, maggi):
    Product.objects.create(shop=shop, name="Tata Salt", aliases=["namak"], low_stock_threshold=2)
    record_entry(shop, EntryKind.STOCK_IN, product=aata, quantity=Decimal("20"))
    record_entry(shop, EntryKind.STOCK_IN, product=maggi, quantity=Decimal("3"))

    assert names(api.get("/api/v1/products/?q=namak")) == ["Tata Salt"]
    assert names(api.get("/api/v1/products/?low=true")) == ["Maggi", "Tata Salt"]
    assert names(api.get("/api/v1/products/?low=false")) == ["Aashirvaad Aata 5kg"]
    assert names(api.get("/api/v1/products/?ordering=-stock_level")) == ["Aashirvaad Aata 5kg", "Maggi", "Tata Salt"]


def test_register_opens_a_shop_and_signs_in(db):
    client = APIClient()
    body = {"shop_name": "  Naya   Kirana ", "username": "naya_kirana", "password": "kirana-pass-2026", "preferred_language": "ta-IN"}
    session = client.post("/api/v1/auth/register/", body).json()
    assert session["shop"] == {"name": "Naya Kirana", "preferred_language": "ta-IN", "photo_key": None}
    client.credentials(HTTP_AUTHORIZATION=f"Token {session['token']}")
    assert client.get("/api/v1/parties/").json()["count"] == 0
    again = APIClient().post("/api/v1/auth/register/", {**body, "username": "NAYA_kirana"})
    assert again.status_code == 400 and "username" in again.json()["error"]["details"]
    weak = APIClient().post("/api/v1/auth/register/", {**body, "username": "other", "password": "1234"})
    assert weak.status_code == 400 and "password" in weak.json()["error"]["details"]
