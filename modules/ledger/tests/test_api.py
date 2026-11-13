from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from modules.ledger.choices import EntryKind
from modules.ledger.services import record_entry, reverse_entry
from modules.shops.models import Party, Shop


@pytest.fixture
def api(shop) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=shop.owner).key}")
    return client


def test_manual_entries_and_their_refusals(api, sharma, ramu, maggi):
    credit = api.post("/api/v1/entries/", {"kind": "CREDIT_GIVEN", "party": sharma.pk, "amount": "500"}, format="json")
    assert credit.status_code == 201 and credit.json()["party_name"] == "Sharma ji"

    over = api.post("/api/v1/entries/", {"kind": "PAYMENT_RECEIVED", "party": sharma.pk, "amount": "501"}, format="json")
    assert (over.status_code, over.json()["error"]["code"]) == (409, "OVERPAYMENT")

    too_much = api.post("/api/v1/entries/", {"kind": "STAFF_ADVANCE", "party": ramu.pk, "amount": "9001"}, format="json")
    assert too_much.json()["error"]["code"] == "EXCEEDS_SALARY"
    salary = {"kind": "SALARY_PAID", "party": ramu.pk, "occurred_on": "2026-11-03"}
    unearned = api.post("/api/v1/entries/", {**salary, "amount": "1"}, format="json")
    assert unearned.json()["error"]["code"] == "EXCEEDS_EARNED"
    record_entry(ramu.shop, EntryKind.ATTENDANCE, party=ramu, occurred_on=date(2026, 11, 3))  # 9000 / 30 days
    assert api.post("/api/v1/entries/", {**salary, "amount": "301"}, format="json").status_code == 409
    assert api.post("/api/v1/entries/", {**salary, "amount": "300"}, format="json").status_code == 201

    short = api.post("/api/v1/entries/", {"kind": "STOCK_OUT", "product": maggi.pk, "quantity": "1"}, format="json")
    assert short.json()["error"]["code"] == "NEGATIVE_STOCK"
    assert api.post("/api/v1/entries/", {"kind": "REVERSAL"}, format="json").status_code == 400


def test_entries_of_another_shop_cannot_be_written(api, db):
    other = Shop.objects.create(owner=get_user_model().objects.create_user("other"), name="Other")
    stranger = Party.objects.create(shop=other, name="Stranger")
    response = api.post("/api/v1/entries/", {"kind": "CREDIT_GIVEN", "party": stranger.pk, "amount": "10"}, format="json")
    assert response.status_code == 404


def test_entry_list_search_kinds_order_and_running_balance(api, shop, sharma, verma, maggi):
    first = record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=Decimal("500"))
    wrong = record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=Decimal("900"))
    reverse_entry(wrong)
    record_entry(shop, EntryKind.PAYMENT_RECEIVED, party=sharma, amount=Decimal("200"))
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=verma, amount=Decimal("50"))
    record_entry(shop, EntryKind.STOCK_IN, product=maggi, quantity=Decimal("12"))

    ledger = api.get(f"/api/v1/entries/?party={sharma.pk}&ordering=id").json()["results"]
    assert [(r["kind"], r["running_balance"]) for r in ledger] == [
        ("CREDIT_GIVEN", 500),
        ("CREDIT_GIVEN", None),
        ("REVERSAL", None),
        ("PAYMENT_RECEIVED", 300),
    ]
    moves = api.get("/api/v1/entries/?kind=STOCK_IN,STOCK_OUT").json()["results"]
    assert [r["product_name"] for r in moves] == ["Maggi"]
    assert api.get("/api/v1/entries/?q=verma").json()["count"] == 1
    assert api.get(f"/api/v1/entries/?q=%23{first.pk}").json()["results"][0]["id"] == first.pk
    by_amount = api.get("/api/v1/entries/?kind=CREDIT_GIVEN&ordering=-amount").json()["results"]
    assert [r["amount"] for r in by_amount] == [900, 500, 50]
    assert api.get("/api/v1/entries/?kind=NOPE").status_code == 400


def test_summary_has_the_dashboard_figures(api, shop, sharma):
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=Decimal("700"))
    body = api.get("/api/v1/summary/").json()
    assert (body["total_outstanding"], body["owing_count"], body["customer_count"]) == (700, 1, 1)
    assert len(body["daily"]) == 14 and body["daily"][-1]["credit"] == 700
    assert body["week_credit"] == 700 and body["recent"][0]["amount"] == 700


def test_summary_splits_dues_by_how_long_the_khata_has_been_quiet(api, shop, sharma, verma):
    from datetime import timedelta

    from django.utils import timezone

    today = timezone.localdate()
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=Decimal("700"))
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=verma, amount=Decimal("300"), occurred_on=today - timedelta(45))
    ages = api.get("/api/v1/summary/").json()["dues_by_age"]
    assert [(a["max_days"], a["count"], a["amount"]) for a in ages] == [(7, 1, 700), (30, 0, 0), (90, 1, 300), (None, 0, 0)]
