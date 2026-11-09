import httpx
import pytest

from modules.voice import intent
from modules.voice.choices import Intent
from modules.voice.intent import GroqIntentParser, ShopContext
from modules.voice.providers import ProviderError

REQUEST = httpx.Request("POST", "https://llm.test/chat/completions")


def reply(status: int, content: str = "", headers: dict | None = None) -> httpx.Response:
    body = {"choices": [{"message": {"content": content}}]} if status == 200 else {"error": {"message": "x"}}
    return httpx.Response(status, json=body, headers=headers, request=REQUEST)


@pytest.fixture
def llm(monkeypatch, settings):
    settings.LLM_API_KEY = "test-key"
    monkeypatch.setattr(intent.time, "sleep", lambda seconds: None)

    def returns(*responses):
        queue = list(responses)
        monkeypatch.setattr(intent.httpx, "post", lambda *args, **kwargs: queue.pop(0))
        return queue

    return returns


def test_rate_limit_is_retried_once(llm):
    llm(reply(429, headers={"retry-after": "2"}), reply(200, '{"intent": "QUERY_LOW_STOCK"}'))
    assert GroqIntentParser().parse("kya kam hai", ShopContext()).intent == Intent.QUERY_LOW_STOCK


def test_second_rate_limit_becomes_provider_error(llm):
    llm(reply(429), reply(429))
    with pytest.raises(ProviderError, match="LLM_HTTP_429"):
        GroqIntentParser().parse("kya kam hai", ShopContext())


def test_invalid_json_is_retried_then_unknown(llm):
    remaining = llm(reply(200, '{"intent": "NOPE"}'), reply(200, "not json"))
    assert GroqIntentParser().parse("hmm", ShopContext()).intent == Intent.UNKNOWN
    assert remaining == []
