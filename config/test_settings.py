import os

os.environ.setdefault("SECRET_KEY", "test-only-not-a-secret")

from config.settings import *  # noqa: E402,F403

STT_PROVIDER = "fake"
TTS_PROVIDER = "fake"
LLM_PROVIDER = "fake"
GNANI_API_KEY = ""
LLM_API_KEY = ""
MEDIA_ROOT = BASE_DIR / ".test-media"  # noqa: F405
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
