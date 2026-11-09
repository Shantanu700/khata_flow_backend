from decimal import Decimal

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from base.enums import Status
from modules.ledger.choices import EntryKind
from modules.ledger.services import record_entry
from modules.shops.models import Party


@pytest.fixture
def api(shop) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=shop.owner).key}")
    return client


def test_update_marks_updated_and_delete_is_soft(api, shop, sharma):
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=Decimal("500"))

    body = api.patch(f"/api/v1/parties/{sharma.pk}/", {"aliases": ["Sharma Uncle"]}, format="json").json()
    assert (body["status"], body["aliases"]) == (Status.UPDATED, ["sharma uncle"])

    assert api.delete(f"/api/v1/parties/{sharma.pk}/").status_code == 204
    assert api.get("/api/v1/parties/").json()["count"] == 0
    assert Party._base_manager.get(pk=sharma.pk).status == Status.DELETED
    assert api.get("/api/v1/entries/").json()["results"][0]["party_name"] == "Sharma ji"

    again = api.post("/api/v1/parties/", {"name": "Sharma ji"}, format="json")
    assert again.status_code == 201 and again.json()["balance"] == 0


def test_duplicate_live_name_is_a_validation_error(api, sharma):
    response = api.post("/api/v1/parties/", {"name": "sharma ji"}, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["details"]["name"] == ["That name is already used in this shop."]
