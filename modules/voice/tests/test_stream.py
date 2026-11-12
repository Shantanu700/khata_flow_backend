import asyncio
import json

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from modules.voice import stream


class FakeGnani:
    "Answers one phrase once the browser commits, then ends, like Prisma after the last segment."

    def __init__(self):
        self.sent, self.headers, self.committed, self.closed = [], {}, asyncio.Event(), False

    async def send(self, data):
        self.sent.append(data)
        if isinstance(data, str) and json.loads(data) == {"type": "commit"}:
            self.committed.set()

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self._events()

    async def _events(self):
        yield json.dumps({"type": "speech_start"})
        await self.committed.wait()
        yield json.dumps({"type": "transcript", "text": "Sharma ji ka 500 udhaar", "is_final": True})


def run(query: str, incoming: list[dict], gnani: FakeGnani | None, monkeypatch) -> list[dict]:
    async def connect(url, additional_headers, open_timeout):
        gnani.headers = additional_headers
        return gnani

    monkeypatch.setattr(stream.websockets, "connect", connect)
    sent: list[dict] = []

    async def go():
        queue = asyncio.Queue()
        for message in incoming:
            queue.put_nowait(message)

        async def send(message):
            sent.append(message)

        await stream.stream({"type": "websocket", "query_string": query.encode()}, queue.get, send)

    asyncio.run(go())
    return sent


def test_ticket_needs_sign_in_and_points_at_the_stream(shop):
    client = APIClient()
    assert client.post("/api/v1/voice/stream-ticket/").status_code == 401
    client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=shop.owner).key}")
    body = client.post("/api/v1/voice/stream-ticket/").json()
    assert body["url"].startswith("wss://") and body["ticket"]


def test_bad_ticket_is_refused_before_accepting(monkeypatch):
    sent = run("ticket=forged&language=hi-IN", [{"type": "websocket.connect"}], None, monkeypatch)
    assert sent == [{"type": "websocket.close", "code": stream.UNAUTHORISED}]


@pytest.mark.django_db(transaction=True)
def test_relays_frames_and_sends_back_phrases(shop, sharma, monkeypatch):
    gnani = FakeGnani()
    incoming = [
        {"type": "websocket.connect"},
        {"type": "websocket.receive", "bytes": b"\x00" * stream.FRAME_BYTES},
        {"type": "websocket.receive", "bytes": b"\x00" * 10},
        {"type": "websocket.receive", "text": "commit"},
    ]
    sent = run(f"ticket={stream.ticket(shop)}&language=hi-Latn", incoming, gnani, monkeypatch)
    assert sent[0] == {"type": "websocket.accept"}
    assert {"type": "websocket.send", "text": json.dumps({"text": "Sharma ji ka 500 udhaar"})} in sent
    assert sent[-1]["type"] == "websocket.close"
    assert gnani.sent[0] == b"\x00" * stream.FRAME_BYTES and len(gnani.sent) == 2
    assert gnani.headers["language_code"] == "hi-IN,en-IN" and "Sharma ji" in json.loads(gnani.headers["keyterms"])
    assert gnani.closed
