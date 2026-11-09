from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from modules.ledger.choices import EntryKind
from modules.ledger.models import Entry
from modules.ledger.services import customer_balance, record_entry, stock_level
from modules.shops.models import Party, Shop
from modules.voice.choices import CommandStatus, Intent
from modules.voice.intent import FakeIntentParser, ParsedCommand
from modules.voice.models import VoiceCommand
from modules.voice.providers import ProviderError
from modules.voice.providers.fake import FakeSTT, silent_wav

D = Decimal


@pytest.fixture
def api(shop) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=shop.owner).key}")
    return client


@pytest.fixture
def llm(monkeypatch):
    def says(intent: Intent, **fields) -> None:
        monkeypatch.setattr(FakeIntentParser, "result", ParsedCommand(intent=intent, **fields))

    return says


def text(api, words: str = "kuch bhi") -> dict:
    response = api.post("/api/v1/voice/text-commands/", {"text": words}, format="json")
    assert response.status_code == 201, response.content
    return response.json()


@pytest.fixture
def stocked(shop, aata, maggi):
    record_entry(shop, EntryKind.STOCK_IN, product=aata, quantity=D("14"))
    record_entry(shop, EntryKind.STOCK_IN, product=maggi, quantity=D("4"))


def test_add_credit_writes_entry_and_speaks(api, llm, sharma):
    llm(Intent.ADD_CREDIT, party_name="Sharma ji", amount=D("500"))
    body = text(api, "Sharma ji ka paanch sau udhaar likh do")
    assert body["stage"] == CommandStatus.COMMITTED
    assert body["response_text"] == "Theek hai. Sharma ji ka ₹500 udhaar likh diya. Ab kul baaki ₹500 hai."
    assert body["entry"]["kind"] == EntryKind.CREDIT_GIVEN and body["entry"]["amount"] == 500
    assert body["response_audio_url"].endswith(f"/api/v1/voice/commands/{body['id']}/audio/")
    assert set(body["timings_ms"]) == {"intent", "resolve", "commit", "tts"}


def test_record_payment(api, llm, shop, sharma):
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=D("550"))
    llm(Intent.RECORD_PAYMENT, party_name="sharma", amount=D("200"))
    assert text(api)["response_text"] == "Sharma ji se ₹200 mil gaye. Ab kul baaki ₹350 hai."


def test_stock_in_and_out(api, llm, stocked, aata):
    llm(Intent.STOCK_IN, product_name="aata", quantity=D("20"))
    assert text(api)["response_text"] == "Aashirvaad Aata 5kg ke 20 packet jod diye. Ab stock 34 packet."
    llm(Intent.STOCK_OUT, product_name="Aashirvaad Aata 5kg", quantity=D("4"))
    assert text(api)["response_text"].endswith("Ab stock 30 packet.")


def test_stock_out_past_zero_is_refused(api, llm, stocked, maggi):
    llm(Intent.STOCK_OUT, product_name="Maggi", quantity=D("9"))
    body = text(api)
    assert body["stage"] == CommandStatus.FAILED and body["error_code"] == "NEGATIVE_STOCK"
    assert body["response_text"] == "Maggi ka stock sirf 4 packet hai. Itna nahi nikal sakta."
    assert stock_level(maggi) == D("4")


def test_attendance_then_again(api, llm, ramu):
    llm(Intent.MARK_ATTENDANCE, party_name="Ramu")
    assert text(api)["response_text"] == "Ramu ki haaziri lag gayi."
    assert text(api)["response_text"] == "Ramu ki haaziri pehle se lagi hai."
    assert Entry.objects.filter(kind=EntryKind.ATTENDANCE).count() == 1


def test_staff_advance_and_salary(api, llm, ramu):
    llm(Intent.STAFF_ADVANCE, party_name="Ramu", amount=D("500"))
    assert text(api)["response_text"].startswith("Ramu ko ₹500 advance likh diya.")
    llm(Intent.SALARY_PAID, party_name="Ramu", amount=D("1000"))
    assert text(api)["response_text"].startswith("Ramu ko ₹1,000 tankhwah likh diya.")


def test_queries(api, llm, shop, sharma, verma, ramu, stocked):
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=D("1250"))
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=verma, amount=D("100000"))
    record_entry(shop, EntryKind.ATTENDANCE, party=ramu)
    cases = [
        (dict(intent=Intent.QUERY_CUSTOMER_BALANCE, party_name="Sharma"), "Sharma ji ka ₹1,250 baaki hai."),
        (dict(intent=Intent.QUERY_TOTAL_OUTSTANDING), "Kul ₹1,01,250 udhaar baaki hai, 2 logon par."),
        (dict(intent=Intent.QUERY_LOW_STOCK), "Ek cheez kam hai: Maggi."),
        (dict(intent=Intent.QUERY_STOCK_LEVEL, product_name="atta"), "Aashirvaad Aata 5kg ka stock 14 packet hai."),
    ]
    for fields, expected in cases:
        llm(**fields)
        body = text(api)
        assert (body["stage"], body["response_text"]) == (CommandStatus.ANSWERED, expected)
    llm(Intent.QUERY_STAFF_DUE, party_name="Ramu")
    assert text(api)["response_text"].startswith("Ramu is mahine 1 din aaye.")


def test_undo_last(api, llm, shop, sharma):
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=D("500"))
    llm(Intent.UNDO_LAST)
    body = text(api, "pichhli entry hata do")
    assert body["response_text"] == "Pichhli entry hata di: Sharma ji ka ₹500 udhaar."
    assert body["entry"]["kind"] == EntryKind.REVERSAL
    assert customer_balance(sharma) == 0


def test_ambiguous_name_round_trip(api, llm, sharma, verma):
    llm(Intent.ADD_CREDIT, party_name="Varma ji", amount=D("300"))
    body = text(api)
    assert body["stage"] == CommandStatus.NEEDS_CLARIFICATION
    assert body["response_text"] == "Verma ji ya Sharma ji?"
    assert [o["id"] for o in body["clarification"]["options"]] == [f"party:{verma.pk}", f"party:{sharma.pk}"]
    response = api.post(f"/api/v1/voice/commands/{body['id']}/clarify/", {"option_id": f"party:{verma.pk}"})
    assert response.json()["stage"] == CommandStatus.COMMITTED
    assert customer_balance(verma) == D("300") and customer_balance(sharma) == 0


def test_unknown_customer_can_be_created(api, llm, sharma):
    llm(Intent.ADD_CREDIT, party_name="Pandey ji", amount=D("150"))
    body = text(api)
    assert body["clarification"]["kind"] == "create_party"
    assert body["response_text"] == "Ye naam nahi mila: Pandey ji. Naya customer banaun?"
    done = api.post(f"/api/v1/voice/commands/{body['id']}/clarify/", {"confirm": True}, format="json").json()
    assert done["stage"] == CommandStatus.COMMITTED
    assert customer_balance(Party.objects.get(name="Pandey ji")) == D("150")


def test_declining_a_clarification_writes_nothing(api, llm, sharma):
    llm(Intent.ADD_CREDIT, party_name="Pandey ji", amount=D("150"))
    body = text(api)
    done = api.post(f"/api/v1/voice/commands/{body['id']}/clarify/", {"confirm": False}, format="json").json()
    assert (done["stage"], done["response_text"]) == (CommandStatus.ANSWERED, "Theek hai, kuch nahi likha.")
    assert not Entry.objects.exists() and not Party.objects.filter(name="Pandey ji").exists()


def test_large_amount_needs_confirmation(api, llm, sharma, settings):
    settings.LEDGER_AMOUNT_CAP = 50000
    llm(Intent.ADD_CREDIT, party_name="Sharma ji", amount=D("75000"))
    body = text(api)
    assert body["response_text"] == "₹75,000 bahut zyada hai. Pakka likh doon?"
    api.post(f"/api/v1/voice/commands/{body['id']}/clarify/", {"option_id": "yes"})
    assert customer_balance(sharma) == D("75000")


def test_expired_clarification_fails_politely(api, llm, sharma, verma):
    llm(Intent.ADD_CREDIT, party_name="Varma ji", amount=D("300"))
    body = text(api)
    command = VoiceCommand.objects.get(pk=body["id"])
    command.clarification["expires_at"] = (timezone.now() - timedelta(seconds=1)).isoformat()
    command.save()
    done = api.post(f"/api/v1/voice/commands/{body['id']}/clarify/", {"option_id": f"party:{verma.pk}"}).json()
    assert done["error_code"] == "CLARIFICATION_EXPIRED"
    assert not Entry.objects.exists()


def test_invalid_option_is_a_400_shape_error(api, llm, sharma, verma):
    llm(Intent.ADD_CREDIT, party_name="Varma ji", amount=D("300"))
    body = text(api)
    response = api.post(f"/api/v1/voice/commands/{body['id']}/clarify/", {"option_id": "party:999"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_OPTION"


def test_unknown_intent_and_missing_amount_fall_back(api, llm, sharma):
    llm(Intent.UNKNOWN)
    assert text(api)["response_text"] == "Maaf kijiye, samajh nahi aaya. Dobara boliye."
    llm(Intent.ADD_CREDIT, party_name="Sharma ji")
    assert text(api)["error_code"] == "MISSING_AMOUNT"


def test_audio_command_runs_stt(api, llm, monkeypatch, sharma):
    monkeypatch.setattr(FakeSTT, "transcript", "Sharma ji ka 500 udhaar likh do")
    llm(Intent.ADD_CREDIT, party_name="Sharma ji", amount=D("500"))
    clip = SimpleUploadedFile("clip.webm", silent_wav(seconds=1.0), content_type="audio/webm")
    body = api.post("/api/v1/voice/commands/", {"audio": clip}, format="multipart").json()
    assert body["transcript"] == "Sharma ji ka 500 udhaar likh do"
    assert body["stage"] == CommandStatus.COMMITTED
    assert {"normalise", "stt"} <= set(body["timings_ms"])


def test_provider_failure_is_spoken_not_500(api, llm, monkeypatch, sharma):
    def broken(*args, **kwargs):
        raise ProviderError("GNANI_STT_TIMEOUT", "ReadTimeout")

    monkeypatch.setattr(FakeSTT, "transcribe", broken)
    clip = SimpleUploadedFile("clip.wav", silent_wav(seconds=1.0), content_type="audio/wav")
    response = api.post("/api/v1/voice/commands/", {"audio": clip}, format="multipart")
    assert response.status_code == 201
    body = response.json()
    assert (body["stage"], body["error_code"]) == (CommandStatus.FAILED, "GNANI_STT_TIMEOUT")
    assert body["response_text"] == "Maaf kijiye, samajh nahi aaya. Dobara boliye."
    assert body["response_audio_url"]


def test_too_short_audio_is_refused(api, sharma):
    clip = SimpleUploadedFile("clip.wav", silent_wav(seconds=0.1), content_type="audio/wav")
    body = api.post("/api/v1/voice/commands/", {"audio": clip}, format="multipart").json()
    assert body["error_code"] == "AUDIO_TOO_SHORT"


def test_commands_are_scoped_to_the_owners_shop(api, llm, sharma, django_user_model):
    llm(Intent.QUERY_TOTAL_OUTSTANDING)
    body = text(api)
    stranger = APIClient()
    user = django_user_model.objects.create_user(username="other", password="other-pass-123")
    stranger.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    assert stranger.get(f"/api/v1/voice/commands/{body['id']}/").json()["error"]["code"] == "NO_SHOP"
    Shop.objects.create(owner=user, name="Other Store")
    response = stranger.get(f"/api/v1/voice/commands/{body['id']}/")
    assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert stranger.get("/api/v1/parties/").json()["count"] == 0
