from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from base.enums import Status
from modules.ledger.choices import EntryKind
from modules.ledger.services import record_entry
from modules.shops.models import Party, Shop


@pytest.fixture
def api(shop) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=shop.owner).key}")
    return client


def test_update_marks_updated_and_delete_is_soft(api, shop, sharma):
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=Decimal("500"))

    body = api.put(f"/api/v1/parties/{sharma.pk}/", {"name": "Sharma ji", "aliases": ["Sharma Uncle"]}, format="json").json()
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


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def test_party_photo_put_get_replace_delete(api, sharma, media):
    url = f"/api/v1/parties/{sharma.pk}/photo/"
    body = api.put(url, {"photo": SimpleUploadedFile("me.png", PNG)}, format="multipart").json()
    first = body["photo_key"]
    assert first and api.get(f"/api/v1/parties/{sharma.pk}/").json()["photo_key"] == first
    got = api.get(url)
    assert got["Content-Type"] == "image/png" and got["X-Content-Type-Options"] == "nosniff"
    assert b"".join(got.streaming_content) == PNG
    second = api.put(url, {"photo": SimpleUploadedFile("again.png", PNG)}, format="multipart").json()["photo_key"]
    assert second != first and len(list(media.rglob("*.png"))) == 1
    assert api.delete(url).json()["photo_key"] is None
    assert api.get(url).status_code == 404 and not list(media.rglob("*.png"))


def test_photo_must_be_a_real_image(api, sharma, media):
    svg = SimpleUploadedFile("x.png", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
    response = api.put(f"/api/v1/parties/{sharma.pk}/photo/", {"photo": svg}, format="multipart")
    assert response.status_code == 400 and not list(media.rglob("*"))


def test_shop_photo_and_another_shops_party(api, shop, media, django_user_model):
    me = api.put("/api/v1/me/photo/", {"photo": SimpleUploadedFile("s.png", PNG)}, format="multipart").json()
    assert me["shop"]["photo_key"] and api.get("/api/v1/me/photo/").status_code == 200
    other = Shop.objects.create(owner=django_user_model.objects.create_user("other", password="x"), name="Other")
    stranger = other.parties.create(name="Stranger")
    assert api.get(f"/api/v1/parties/{stranger.pk}/photo/").status_code == 404
