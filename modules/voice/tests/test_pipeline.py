import json
from datetime import timedelta
from decimal import Decimal

import pytest
from asgiref.sync import async_to_sync
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import AsyncClient
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from modules.ledger.choices import EntryKind
from modules.ledger.models import Entry
from modules.ledger.services import customer_balance, record_entry, stock_level
from modules.shops.choices import PartyKind
from modules.shops.models import Party, Shop
from modules.voice.choices import CommandStatus, Intent
from modules.voice.intent import FakeIntentParser, ParsedCommand
from modules.voice.models import VoiceCommand
from modules.voice.pipeline import handle_command
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
    "A message in a new chat."
    response = api.post("/api/v1/voice/text-commands/", {"text": words}, format="json")
    assert response.status_code == 201, response.content
    return response.json()


def reply(api, body: dict, words: str) -> dict:
    "The next message in the chat `body` is in."
    response = api.post("/api/v1/voice/text-commands/", {"text": words, "chat": body["chat"]}, format="json")
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


def _present(shop, ramu, days: int) -> None:
    for day in range(1, days + 1):
        record_entry(shop, EntryKind.ATTENDANCE, party=ramu, occurred_on=timezone.localdate().replace(day=day))


def test_staff_advance_and_salary(api, llm, shop, ramu):
    llm(Intent.STAFF_ADVANCE, party_name="Ramu", amount=D("500"))
    assert text(api)["response_text"].startswith("Ramu ko ₹500 advance likh diya.")
    llm(Intent.SALARY_PAID, party_name="Ramu", amount=D("1000"))
    refused = text(api)
    assert (refused["stage"], refused["response_text"]) == (CommandStatus.FAILED, "Is mahine ki kamaai mein se sirf ₹0 dena baaki hai.")
    _present(shop, ramu, 6)
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


def test_undo_is_never_done_by_voice(api, llm, shop, sharma):
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=D("500"))
    llm(Intent.UNDO_LAST)
    body = text(api, "Gupta aunty ke 750 wapas karo")
    assert body["stage"] == CommandStatus.ANSWERED and body["entry"] is None
    assert body["response_text"].startswith("Awaaz se entry nahi palat sakta.")
    assert customer_balance(sharma) == 500 and Entry.objects.count() == 1


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


def test_shop_overview_questions(api, llm, shop, sharma, verma, ramu):
    shyam = Party.objects.create(shop=shop, name="Shyam", kind=PartyKind.STAFF)
    Party.objects.create(shop=shop, name="Gone", kind=PartyKind.STAFF, is_active=False)
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=D("500"))
    record_entry(shop, EntryKind.ATTENDANCE, party=ramu)
    cases = [
        (Intent.QUERY_STAFF_LIST, "Dukaan mein 2 log kaam karte hain: Ramu aur Shyam."),
        (Intent.QUERY_ATTENDANCE_TODAY, "Aaj Ramu aaye hain. Shyam abhi nahi aaye."),
        (Intent.QUERY_CUSTOMER_COUNT, "Aapke 2 customer hain, 1 par udhaar baaki hai."),
    ]
    for intent, expected in cases:
        llm(intent)
        body = text(api)
        assert (body["stage"], body["response_text"]) == (CommandStatus.ANSWERED, expected)
    record_entry(shop, EntryKind.ATTENDANCE, party=shyam)
    llm(Intent.QUERY_ATTENDANCE_TODAY)
    assert text(api)["response_text"] == "Aaj sab aaye hain: Ramu aur Shyam."
    shop.parties.filter(kind=PartyKind.STAFF).update(is_active=False)
    llm(Intent.QUERY_STAFF_LIST)
    assert text(api)["response_text"] == "Abhi koi staff nahi joda gaya hai."


def test_staff_due_names_the_month_asked_about(api, llm, ramu):
    last_month = (timezone.localdate().replace(day=1) - timedelta(days=1)).replace(day=1)
    llm(Intent.QUERY_STAFF_DUE, party_name="Ramu", date_hint=last_month.isoformat())
    assert text(api)["response_text"] == "Ramu pichhle mahine 0 din aaye. ₹0 bane, kuch nahi diya, ₹0 dena baaki hai."
    llm(Intent.QUERY_STAFF_DUE, party_name="Ramu", period="LAST_MONTH")
    assert text(api)["response_text"].startswith("Ramu pichhle mahine 0 din aaye.")


def test_aggregate_questions(api, llm, shop, sharma, verma, ramu, aata, maggi):
    today = timezone.localdate()
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=verma, amount=D("1000"), occurred_on=today - timedelta(days=40))
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=D("500"))
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=verma, amount=D("200"))
    record_entry(shop, EntryKind.PAYMENT_RECEIVED, party=sharma, amount=D("100"))
    _present(shop, ramu, 5)
    record_entry(shop, EntryKind.STAFF_ADVANCE, party=ramu, amount=D("300"))
    record_entry(shop, EntryKind.SALARY_PAID, party=ramu, amount=D("1000"))
    record_entry(shop, EntryKind.STOCK_IN, product=maggi, quantity=D("10"))
    record_entry(shop, EntryKind.STOCK_OUT, product=maggi, quantity=D("4"))
    record_entry(shop, EntryKind.STOCK_IN, product=aata, quantity=D("5"))
    cases = [
        (dict(intent=Intent.QUERY_STOCK_LIST), "Stock mein 2 cheezein hain: Aashirvaad Aata 5kg 5 packet aur Maggi 6 packet."),
        (dict(intent=Intent.QUERY_DEBTOR_LIST), "2 logon par udhaar baaki hai: Verma ji ₹1,200 aur Sharma ji ₹400."),
        (dict(intent=Intent.QUERY_CREDIT_GIVEN), "Aaj ₹700 ka udhaar diya gaya."),
        (dict(intent=Intent.QUERY_CREDIT_GIVEN, party_name="Verma"), "Ab tak Verma ji ko ₹1,200 ka udhaar diya gaya."),
        (dict(intent=Intent.QUERY_PAYMENTS_RECEIVED, period="THIS_WEEK"), "Is hafte ₹100 ki vasooli hui."),
        (dict(intent=Intent.QUERY_PAYMENTS_RECEIVED, party_name="Sharma ji", period="LAST_MONTH"), "Pichhle mahine Sharma ji se ₹0 mile."),
        (dict(intent=Intent.QUERY_STAFF_PAID), "Aaj staff ko ₹1,300 diye gaye: ₹300 advance, ₹1,000 tankhwah."),
        (dict(intent=Intent.QUERY_STAFF_PAID, party_name="Ramu", period="THIS_MONTH"), "Is mahine Ramu ko ₹1,300 diye gaye: ₹300 advance, ₹1,000 tankhwah."),
        (dict(intent=Intent.QUERY_STOCK_MOVEMENT, product_name="maggi"), "Aaj Maggi ke 10 packet aaye aur 4 packet nikle."),
        (
            dict(intent=Intent.QUERY_STOCK_MOVEMENT),
            "Aaj 2 cheezon mein aana-jaana hua: Aashirvaad Aata 5kg: 5 packet aaye, 0 packet nikle; Maggi: 10 packet aaye, 4 packet nikle.",
        ),
        (dict(intent=Intent.QUERY_STOCK_MOVEMENT, period="YESTERDAY"), "Kal stock mein kuch aaya ya nikla nahi."),
    ]
    for fields, expected in cases:
        llm(**fields)
        body = text(api)
        assert (body["stage"], body["response_text"]) == (CommandStatus.ANSWERED, expected), fields


def test_query_for_unknown_customer_does_not_offer_to_create(api, llm, sharma):
    llm(Intent.QUERY_CREDIT_GIVEN, party_name="Pandey ji")
    body = text(api)
    assert body["stage"] == CommandStatus.FAILED and body["clarification"] is None


@pytest.mark.django_db(transaction=True)
def test_progress_streams_each_stage_then_the_command(api, llm, sharma):
    llm(Intent.QUERY_CUSTOMER_BALANCE, party_name="Sharma ji")
    response = api.post("/api/v1/voice/text-commands/?progress=1", {"text": "Sharma ji ka kitna"}, format="json")
    assert response["Content-Type"] == "application/x-ndjson"
    events = [json.loads(line) for line in b"".join(response.streaming_content).decode().splitlines()]
    assert events[0] == {"id": events[-1]["command"]["id"]}
    assert [e["stage"] for e in events[1:-1]] == ["intent", "resolve", "commit", "tts"]
    assert all(isinstance(e["ms"], int) for e in events[1:-1])
    assert events[-1]["command"]["stage"] == CommandStatus.ANSWERED
    assert events[-1]["command"]["response_text"] == "Sharma ji ka koi baaki nahi hai."


@pytest.mark.django_db(transaction=True)
def test_progress_streams_line_by_line_under_asgi(llm, shop, sharma):
    "Production runs uvicorn: a sync iterator there is collected whole, so every stage would land at once."
    llm(Intent.QUERY_CUSTOMER_BALANCE, party_name="Sharma ji")
    token = Token.objects.create(user=shop.owner).key

    async def stream():
        response = await AsyncClient().post(
            "/api/v1/voice/text-commands/?progress=1",
            {"text": "Sharma ji ka kitna"},
            content_type="application/json",
            headers={"authorization": f"Token {token}"},
        )
        assert response.is_async and response["X-Accel-Buffering"] == "no"
        return [json.loads(chunk) async for chunk in response.streaming_content]

    events = async_to_sync(stream)()
    assert [e.get("stage") for e in events[1:-1]] == ["intent", "resolve", "commit", "tts"]
    assert events[-1]["command"]["response_text"] == "Sharma ji ka koi baaki nahi hai."


@pytest.mark.django_db(transaction=True)
def test_progress_reports_a_refused_answer_as_an_error_line(api, llm, sharma):
    llm(Intent.QUERY_CUSTOMER_BALANCE, party_name="Sharma ji")
    command = text(api)
    response = api.post(f"/api/v1/voice/commands/{command['id']}/clarify/?progress=1", {"confirm": True}, format="json")
    events = [json.loads(line) for line in b"".join(response.streaming_content).decode().splitlines()]
    assert events == [
        {"id": command["id"]},
        {"error": {"code": "NOT_WAITING", "message": "This command is not waiting for an answer."}},
    ]


@pytest.mark.django_db(transaction=True)
def test_progress_carries_a_written_entry(api, llm, sharma):
    llm(Intent.ADD_CREDIT, party_name="Sharma ji", amount=D("500"))
    response = api.post("/api/v1/voice/text-commands/?progress=1", {"text": "Sharma ji ka 500 udhaar"}, format="json")
    last = json.loads(b"".join(response.streaming_content).decode().splitlines()[-1])
    assert last["command"]["stage"] == CommandStatus.COMMITTED and last["command"]["entry"]["amount"] == 500


def test_cancel_before_the_write_leaves_the_ledger_alone(api, llm, sharma, shop):
    llm(Intent.ADD_CREDIT, party_name="Sharma ji", amount=D("500"))
    command = VoiceCommand.objects.create(shop=shop, input_mode="TEXT", transcript="Sharma ji ka 500", language="hi-Latn")
    assert api.post(f"/api/v1/voice/commands/{command.pk}/cancel/").json()["stage"] == CommandStatus.CANCELLED
    handle_command(command)
    command.refresh_from_db()
    assert command.stage == CommandStatus.CANCELLED and not command.response_audio
    assert not Entry.objects.exists() and customer_balance(sharma) == 0


def test_cancel_after_the_write_reverses_it(api, llm, sharma):
    llm(Intent.ADD_CREDIT, party_name="Sharma ji", amount=D("500"))
    command = text(api)
    assert customer_balance(sharma) == 500
    assert api.post(f"/api/v1/voice/commands/{command['id']}/cancel/").json()["stage"] == CommandStatus.CANCELLED
    assert customer_balance(sharma) == 0
    assert Entry.objects.filter(kind=EntryKind.REVERSAL, voice_command_id=command["id"]).count() == 1


def clarify(api, body: dict, **answer) -> dict:
    return api.post(f"/api/v1/voice/commands/{body['id']}/clarify/", answer, format="json").json()


def test_new_customer_is_added_only_after_confirming(api, llm, sharma):
    llm(Intent.ADD_CUSTOMER, party_name="Rahul Verma", amount=D("300"))
    body = text(api, "Naya customer Rahul Verma, pichhla udhaar 300")
    assert body["clarification"]["kind"] == "confirm_create"
    assert body["response_text"] == "Naya customer Rahul Verma, pichhla udhaar ₹300. Jod doon?"
    assert not Party.objects.filter(name="Rahul Verma").exists()
    done = clarify(api, body, confirm=True)
    assert done["stage"] == CommandStatus.COMMITTED
    assert done["response_text"] == "Rahul Verma ka khata khul gaya. Ab kul baaki ₹300 hai."
    assert customer_balance(Party.objects.get(name="Rahul Verma", kind=PartyKind.CUSTOMER)) == D("300")


def test_declining_a_new_entity_adds_nothing(api, llm, shop):
    llm(Intent.ADD_STAFF, party_name="Raju", amount=D("8000"))
    body = text(api)
    assert body["response_text"] == "Naya staff Raju, salary ₹8,000 mahina. Jod doon?"
    clarify(api, body, confirm=False)
    assert not Party.objects.filter(name="Raju").exists()


def test_missing_details_are_asked_for_then_added(api, llm, shop):
    llm(Intent.ADD_STAFF, party_name="Raju")
    body = text(api, "Naya staff Raju jodo")
    assert (body["stage"], body["clarification"]["kind"]) == (CommandStatus.NEEDS_CLARIFICATION, "fill")
    assert body["response_text"] == "Raju ki mahine ki salary kitni hai?"
    assert [o["id"] for o in body["clarification"]["options"]] == ["no"]
    llm(Intent.ADD_STAFF, party_name="Raju", amount=D("8000"))
    answer = reply(api, body, "aath hazaar")
    assert answer["parsed"]["amount"] == "8000" and answer["clarification"]["kind"] == "confirm_create"
    assert VoiceCommand.objects.get(pk=body["id"]).stage == CommandStatus.ANSWERED
    done = clarify(api, answer, confirm=True)
    assert done["response_text"] == "Raju staff mein jud gaye. Salary ₹8,000 mahina."
    assert Party.objects.get(name="Raju").monthly_salary == D("8000")


def test_answer_is_parsed_with_the_chat_so_far(api, llm, shop):
    llm(Intent.ADD_PRODUCT, product_name="Maggi Masala")
    body = text(api, "Naya saamaan Maggi Masala")
    assert body["response_text"] == "Maggi Masala kis mein ginte hain: packet, kilo, litre ya piece?"
    llm(Intent.ADD_PRODUCT, product_name="Maggi Masala", unit="packets", quantity=D("12"))
    answer = reply(api, body, "packet, 12 hain")
    assert FakeIntentParser.seen == ("packet, 12 hain", [("Naya saamaan Maggi Masala", body["response_text"])])
    assert answer["response_text"].startswith("Naya saamaan Maggi Masala, packet mein, abhi 12 packet stock")
    clarify(api, answer, confirm=True)
    assert stock_level(shop.products.get(name="Maggi Masala")) == D("12")


def test_an_answer_to_a_closed_question_is_a_fresh_command(api, llm, shop, sharma):
    llm(Intent.ADD_CUSTOMER)
    body = text(api)
    assert body["response_text"] == "Naye customer ka naam kya hai?"
    clarify(api, body, confirm=False)
    llm(Intent.QUERY_CUSTOMER_BALANCE, party_name="Sharma ji")
    answer = reply(api, body, "Sharma ji ka hisaab")
    assert answer["stage"] == CommandStatus.ANSWERED and answer["transcript"] == "Sharma ji ka hisaab"


def test_an_existing_name_is_not_added_twice(api, llm, sharma):
    llm(Intent.ADD_CUSTOMER, party_name="sharma ji")
    body = text(api)
    assert (body["error_code"], body["response_text"]) == ("ALREADY_EXISTS", "sharma ji pehle se hai. Doosra naam boliye.")


def test_product_without_opening_stock_is_confirmed_with_zero(api, llm, shop):
    llm(Intent.ADD_PRODUCT, product_name="Kinder Joy", unit="packet")
    body = text(api, "Naya saamaan Kinder Joy, packet")
    assert body["clarification"]["kind"] == "confirm_create"
    assert body["response_text"].startswith("Naya saamaan Kinder Joy, packet mein, abhi 0 packet stock")


def test_typed_yes_or_no_answers_the_question(api, llm, shop, monkeypatch):
    llm(Intent.ADD_STAFF, party_name="Bebo", amount=D("2000"))
    body = text(api, "Naya staff Bebo, salary 2000")
    parse = lambda self, said, context, history=(): pytest.fail("a yes/no answer is not parsed")  # noqa: E731
    monkeypatch.setattr(FakeIntentParser, "parse", parse)
    done = reply(api, body, "Haan, jod do")
    assert done["stage"] == CommandStatus.COMMITTED and Party.objects.get(name="Bebo").monthly_salary == D("2000")
    assert VoiceCommand.objects.get(pk=body["id"]).stage == CommandStatus.ANSWERED
    monkeypatch.undo()
    llm(Intent.ADD_CUSTOMER, party_name="Mohan")
    body = text(api)
    done = reply(api, body, "no")
    assert done["response_text"] == "Theek hai, kuch nahi likha." and not Party.objects.filter(name="Mohan").exists()


def test_typed_no_cancels_a_missing_detail_question(api, llm, shop):
    llm(Intent.ADD_PRODUCT, product_name="Kinder Joy")
    body = text(api)
    assert body["clarification"]["kind"] == "fill"
    assert reply(api, body, "nahi rehne do")["response_text"] == "Theek hai, kuch nahi likha."


def test_yes_without_a_question_is_a_fresh_command(api, llm, shop):
    assert text(api, "yes")["error_code"] == "UNKNOWN_INTENT"


def test_a_change_at_the_read_back_is_applied_and_read_back_again(api, llm, shop):
    llm(Intent.ADD_STAFF, party_name="Bebo", amount=D("2000"))
    body = text(api, "Naya staff Bebo, salary 2000")
    llm(Intent.ADD_STAFF, party_name="Bebo", amount=D("3000"))
    changed = reply(api, body, "nahi, salary 3000 kar do")
    assert FakeIntentParser.seen == ("nahi, salary 3000 kar do", [("Naya staff Bebo, salary 2000", body["response_text"])])
    assert changed["clarification"]["kind"] == "confirm_create"
    assert changed["response_text"] == "Naya staff Bebo, salary ₹3,000 mahina. Jod doon?"
    assert not Party.objects.filter(name="Bebo").exists()
    reply(api, changed, "haan ji")
    assert Party.objects.get(name="Bebo").monthly_salary == D("3000")


def test_low_stock_alert_can_be_set_by_voice(api, llm, shop):
    llm(Intent.ADD_PRODUCT, product_name="Kurkure", unit="packet", low_stock_alert=D("6"))
    body = text(api)
    assert "6 packet se kam hone par" in body["response_text"]
    reply(api, body, "हाँ")
    assert shop.products.get(name="Kurkure").low_stock_threshold == D("6")


def test_yes_no_needs_a_reply_of_only_yes_or_no_words():
    from modules.voice.pipeline import NO, YES, _yes_no

    assert [_yes_no(t) for t in ["haan jod do", "Yes, please.", "हाँ", "nahi ji", "no thanks"]] == [YES, YES, YES, NO, NO]
    assert [_yes_no(t) for t in ["nahi, salary 3000", "haan naam Bebo Sharma", "Sharma ji ka hisaab", ""]] == [None] * 4
    assert [_yes_no(t) for t in ["रहने दो", "Rehne do", "Cancel", "ರಹನೆ"]] == [NO, NO, NO, None]


def test_a_half_day_by_voice_and_then_a_full_day(api, llm, ramu):
    llm(Intent.MARK_ATTENDANCE, party_name="Ramu", quantity=D("0.5"))
    body = text(api, "Ramu aaj half day aaya")
    assert body["response_text"] == "Ramu ki aadhe din ki haaziri lag gayi."
    assert ledger_due(ramu).days_present == D("0.5")
    llm(Intent.MARK_ATTENDANCE, party_name="Ramu")
    assert text(api, "Ramu aaj poore din raha")["response_text"] == "Ramu ki haaziri lag gayi."
    assert ledger_due(ramu).days_present == 1


def ledger_due(party):
    from modules.ledger.services import staff_due

    return staff_due(party, timezone.localdate())
