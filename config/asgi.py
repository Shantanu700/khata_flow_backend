import os

from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# ponytail: Django serves /static/ itself (only the admin uses it); put WhiteNoise or Caddy in front if traffic grows.
django_app = ASGIStaticFilesHandler(get_asgi_application())

from modules.voice.stream import stream  # noqa: E402  (needs the app registry loaded above)

STREAM_PATH = "/api/v1/voice/stream/"


async def application(scope, receive, send):
    "The live-transcript WebSocket; everything else is the regular Django app."
    if scope["type"] == "websocket" and scope["path"] == STREAM_PATH:
        return await stream(scope, receive, send)
    if scope["type"] == "websocket":
        await receive()
        return await send({"type": "websocket.close", "code": 4404})
    return await django_app(scope, receive, send)
