import json
import re
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from modules.shops.choices import SHOP_LANGUAGES
from modules.shops.models import Shop
from modules.voice import responses
from modules.voice.choices import Intent
from modules.voice.intent import FakeIntentParser, ParsedCommand
from modules.voice.providers.base import Transcript
from modules.voice.providers.fake import FakeSTT, FakeTTS, silent_wav


def client_for(shop) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=shop.owner).key}")
    return client


@pytest.fixture
def api(shop) -> APIClient:
    return client_for(shop)


@pytest.fixture
def credit(monkeypatch):
    monkeypatch.setattr(FakeIntentParser, "result", ParsedCommand(intent=Intent.ADD_CREDIT, party_name="Sharma ji", amount=Decimal("500")))


def test_reply_audio_only_reaches_the_owner(api, shop, sharma, credit, db):
    body = api.post("/api/v1/voice/text-commands/", {"text": "Sharma ji 500"}, format="json").json()
    audio = f"/api/v1/voice/commands/{body['id']}/audio/"
    owner = api.get(audio)
    assert owner.status_code == 200 and owner["Content-Type"] == "audio/wav"
    assert APIClient().get(audio).status_code == 401
    other = Shop.objects.create(owner=get_user_model().objects.create_user("other"), name="Other")
    assert client_for(other).get(audio).status_code == 404


def test_history_lists_the_shops_commands_newest_first(api, sharma, credit):
    for words in ("pehla", "doosra"):
        api.post("/api/v1/voice/text-commands/", {"text": words}, format="json")
    history = api.get("/api/v1/voice/commands/").json()
    assert history["count"] == 2 and [c["transcript"] for c in history["results"]] == ["doosra", "pehla"]


def test_reply_and_speech_follow_the_command_language(api, sharma, credit, monkeypatch):
    spoken = []
    monkeypatch.setattr(FakeTTS, "synthesize", lambda self, text, language: spoken.append(language) or b"RIFF")
    body = api.post("/api/v1/voice/text-commands/", {"text": "x", "language": "en-IN"}, format="json").json()
    assert body["language"] == "en-IN" and spoken == ["en-IN"]
    assert body["response_text"] == responses.catalog("en-IN")["credit_added"].format(
        name="Sharma ji", amount="₹500", balance=responses.catalog("en-IN")["balance.owing"].format(amount="₹500")
    )
    api.post("/api/v1/voice/text-commands/", {"text": "x", "language": "hi-Latn"}, format="json")
    assert spoken[-1] == "hi-IN"


@pytest.mark.parametrize("code", SHOP_LANGUAGES)
def test_every_catalog_has_exactly_the_base_keys(code):
    from modules.i18n.catalogs import DIR as UI

    for folder, base in ((responses.DIR, responses.BASE), (UI, "hi-Latn")):
        path = folder / f"{code}.json"
        assert path.exists(), path
        theirs = json.loads(path.read_text("utf-8"))
        ours = json.loads((folder / f"{base}.json").read_text("utf-8"))
        assert set(theirs) == set(ours), path
        # Same {placeholders} in every translation, so formatting never fails or drops a value.
        holes = {key: sorted(re.findall(r"\{(\w+)\}", text)) for key, text in ours.items()}
        assert {key: sorted(re.findall(r"\{(\w+)\}", text)) for key, text in theirs.items()} == holes, path


def test_a_typed_command_keeps_the_script_it_came_in(api, sharma, credit):
    body = api.post(
        "/api/v1/voice/text-commands/", {"text": "ஷர்மா ஜி 500 உதார்", "language": "en-IN"}, format="json"
    ).json()
    assert body["language"] == "en-IN" and body["detected_language"] == "ta-IN"


def test_latin_text_is_not_judged(api, sharma, credit):
    body = api.post(
        "/api/v1/voice/text-commands/", {"text": "Sharma ji ka 500 udhaar", "language": "hi-IN"}, format="json"
    ).json()
    assert body["detected_language"] == ""


def test_speech_keeps_the_language_prisma_heard(api, sharma, credit, monkeypatch):
    monkeypatch.setattr(
        FakeSTT, "transcribe", lambda *args, **kwargs: Transcript(text="ஷர்மா ஜி 500 உதார்", language="ta-IN")
    )
    clip = SimpleUploadedFile("clip.webm", silent_wav(seconds=1.0), content_type="audio/webm")
    body = api.post("/api/v1/voice/commands/", {"audio": clip, "language": "en-IN"}, format="multipart").json()
    assert body["language"] == "en-IN" and body["detected_language"] == "ta-IN"


@pytest.mark.parametrize("text", ["हाँ", "ஆம்", "500", "OK ji"])
def test_a_word_or_a_number_is_not_judged(text):
    from modules.voice.scripts import script_language

    assert script_language(text) == ""


def test_a_hindi_sentence_is_judged_hindi():
    from modules.voice.scripts import script_language

    assert script_language("शर्मा जी का कितना बाकी है") == "hi-IN"
