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


def test_strict_json_failure_falls_back_to_plain_json(llm, monkeypatch):
    sent = []
    queue = [
        httpx.Response(400, json={"error": {"code": "json_validate_failed"}}, request=REQUEST),
        reply(200, '{"intent": "QUERY_CUSTOMER_BALANCE", "party_name": "Rathore ji"}'),
    ]

    def post(*args, json, **kwargs):
        sent.append(json)
        return queue.pop(0)

    monkeypatch.setattr(intent.httpx, "post", post)
    parsed = GroqIntentParser().parse("Rathore ji ka kitna baaki hai", ShopContext())
    assert (parsed.intent, parsed.party_name) == (Intent.QUERY_CUSTOMER_BALANCE, "Rathore ji")
    assert "response_format" in sent[0] and "response_format" not in sent[1]


def test_daily_limit_switches_to_the_fallback_model(monkeypatch, settings):
    import httpx

    from modules.voice.intent import GroqIntentParser, ShopContext

    settings.LLM_API_KEY, settings.LLM_MODEL, settings.LLM_FALLBACK_MODEL = "k", "big", "small"
    asked = []

    def post(url, headers, json, timeout):
        asked.append(json["model"])
        if json["model"] == "big":
            return httpx.Response(429, text='{"error":{"message":"Rate limit ... tokens per day (TPD): Limit 200000"}}')
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"intent": "UNKNOWN", "party_name": null, "product_name": null, "amount": null, "quantity": null, "unit": null, "date_hint": null, "period": null}'}}]})

    monkeypatch.setattr(httpx, "post", post)
    assert GroqIntentParser().parse("namaste", ShopContext()).intent == "UNKNOWN"
    assert asked == ["big", "small"]
