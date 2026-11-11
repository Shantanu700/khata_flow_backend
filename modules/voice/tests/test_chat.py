from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from modules.ledger.services import customer_balance
from modules.shops.models import Shop
from modules.voice.choices import CommandStatus, InputMode, Intent
from modules.voice.intent import FakeIntentParser
from modules.voice.models import Chat, VoiceCommand
from modules.voice.pipeline import HISTORY_TURNS
from modules.voice.tests.test_pipeline import api, llm, reply, text  # noqa: F401

D = Decimal


def test_which_one_can_be_answered_by_name(api, llm, sharma, verma):  # noqa: F811
    llm(Intent.ADD_CREDIT, party_name="Varma ji", amount=D("300"))
    body = text(api)
    assert body["clarification"]["kind"] == "choose"
    done = reply(api, body, "Verma ji wale")
    assert done["stage"] == CommandStatus.COMMITTED
    assert customer_balance(verma) == D("300") and customer_balance(sharma) == 0


def test_a_tapped_option_label_picks_it_even_when_names_look_alike(api, llm, shop):  # noqa: F811
    from modules.shops.models import Party

    first = Party.objects.create(shop=shop, name="Sharma ji")
    Party.objects.create(shop=shop, name="Sharma uncle")
    llm(Intent.ADD_CREDIT, party_name="Sharma", amount=D("100"))
    body = text(api)
    assert {o["label"] for o in body["clarification"]["options"]} == {"Sharma ji", "Sharma uncle"}
    assert reply(api, body, "Sharma ji")["stage"] == CommandStatus.COMMITTED
    assert customer_balance(first) == D("100")


def test_a_reply_naming_neither_option_is_a_new_message(api, llm, sharma, verma):  # noqa: F811
    llm(Intent.ADD_CREDIT, party_name="Varma ji", amount=D("300"))
    body = text(api)
    llm(Intent.QUERY_TOTAL_OUTSTANDING)
    assert reply(api, body, "kitna baaki hai sabka")["stage"] == CommandStatus.ANSWERED
    assert VoiceCommand.objects.get(pk=body["id"]).stage == CommandStatus.NEEDS_CLARIFICATION


def test_a_new_chat_starts_with_no_context(api, llm, shop):  # noqa: F811
    llm(Intent.ADD_STAFF, party_name="Raju")
    first = text(api, "Naya staff Raju jodo")
    second = text(api, "aath hazaar")
    assert second["chat"] != first["chat"]
    assert FakeIntentParser.seen == ("aath hazaar", [])
    assert VoiceCommand.objects.get(pk=first["id"]).stage == CommandStatus.NEEDS_CLARIFICATION


def test_history_is_the_last_turns_of_this_chat_only(api, llm, sharma):  # noqa: F811
    llm(Intent.QUERY_CUSTOMER_BALANCE, party_name="Sharma ji")
    body = text(api, "message 0")
    for n in range(1, HISTORY_TURNS + 2):
        reply(api, body, f"message {n}")
    text(api, "another chat")
    reply(api, body, "latest")
    said, history = FakeIntentParser.seen
    assert said == "latest" and len(history) == HISTORY_TURNS
    assert [h[0] for h in history] == [f"message {n}" for n in range(2, HISTORY_TURNS + 2)]


def test_an_old_question_is_not_answered_after_it_expires(api, llm, shop):  # noqa: F811
    llm(Intent.ADD_STAFF, party_name="Raju")
    body = text(api, "Naya staff Raju jodo")
    command = VoiceCommand.objects.get(pk=body["id"])
    command.clarification["expires_at"] = (timezone.now() - timedelta(seconds=1)).isoformat()
    command.save()
    llm(Intent.UNKNOWN)
    assert reply(api, body, "nahi")["error_code"] == "UNKNOWN_INTENT"
    assert VoiceCommand.objects.get(pk=body["id"]).stage == CommandStatus.NEEDS_CLARIFICATION


def test_chats_are_listed_newest_first_for_this_shop_only(api, llm, sharma, django_user_model):  # noqa: F811
    llm(Intent.QUERY_CUSTOMER_BALANCE, party_name="Sharma ji")
    older = text(api, "Sharma ji ka hisaab")
    reply(api, older, "aur Verma ji?")
    newer = text(api, "kitna baaki hai")
    chats = api.get("/api/v1/voice/chats/").json()
    assert [c["id"] for c in chats["results"]] == [newer["chat"], older["chat"]]
    assert chats["results"][1]["title"] == "Sharma ji ka hisaab"
    assert [c["message_count"] for c in chats["results"]] == [1, 2]
    assert chats["results"][0]["last_stage"] == CommandStatus.ANSWERED
    assert chats["results"][0]["last_reply"] == newer["response_text"]
    messages = api.get(f"/api/v1/voice/chats/{older['chat']}/messages/").json()
    assert [m["transcript"] for m in messages["results"]] == ["aur Verma ji?", "Sharma ji ka hisaab"]

    stranger = APIClient()
    user = django_user_model.objects.create_user(username="other", password="other-pass-123")
    Shop.objects.create(owner=user, name="Other Store")
    stranger.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    assert stranger.get("/api/v1/voice/chats/").json()["count"] == 0
    assert stranger.get(f"/api/v1/voice/chats/{older['chat']}/messages/").status_code == 404
    response = stranger.post("/api/v1/voice/text-commands/", {"text": "hi", "chat": older["chat"]}, format="json")
    assert response.status_code == 404


@pytest.mark.django_db
def test_old_commands_are_filed_one_chat_per_day(shop):
    now = timezone.now()
    for days_ago, words in [(2, "a"), (2, "b"), (0, "c")]:
        command = VoiceCommand.objects.create(shop=shop, input_mode=InputMode.TEXT, language="hi-Latn", transcript=words)
        VoiceCommand.objects.filter(pk=command.pk).update(created_at=now - timedelta(days=days_ago))
    call_command("group_old_commands")
    call_command("group_old_commands")
    chats = list(Chat.objects.filter(shop=shop))
    assert [(c.title, c.messages.count()) for c in chats] == [("c", 1), ("a", 2)]
    assert not VoiceCommand.objects.filter(chat=None).exists()
