import os
from pathlib import Path
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# .env wins over the process environment: the dev server re-imports settings when .env changes
# (see base/apps.py) and must not keep the values it inherited from the previous process.
load_dotenv(BASE_DIR / ".env", override=True)


def env_bool(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes")


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DEBUG")

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY is not set. Copy .env.example to .env.")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1" if DEBUG else "")

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    "drf_spectacular",
]

LOCAL_APPS = [
    "base",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

_db = urlparse(os.environ.get("DATABASE_URL", "postgres://khaataflow:khaataflow@db:5432/khaataflow"))

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _db.path.lstrip("/"),
        "USER": _db.username,
        "PASSWORD": _db.password,
        "HOST": _db.hostname,
        "PORT": _db.port or 5432,
    }
}

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_SERIALIZER = "json"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "base.openapi.BaseSchema",
    "EXCEPTION_HANDLER": "base.exceptions.exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.TokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["base.permissions.APIAuthenticationPermission"],
    "DEFAULT_PAGINATION_CLASS": "base.pagination.DefaultPagination",
    "COERCE_DECIMAL_TO_STRING": False,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "KhaataFlow API",
    "DESCRIPTION": "Voice-first shop book for kirana merchants.",
    "VERSION": "0.1.0",
    "OPERATION_ID_METHOD_POSITION": "PRE",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    # redoc@latest is 3.x, whose standalone bundle uses `import.meta` and throws a SyntaxError
    # when loaded by drf-spectacular's classic <script> tag.
    "REDOC_DIST": "https://cdn.jsdelivr.net/npm/redoc@2.5.4",
    "ENUM_NAME_OVERRIDES": {
        "EntryKindEnum": "modules.ledger.choices.EntryKind",
        "CommandStatusEnum": "modules.voice.choices.CommandStatus",
        "RecordStatusEnum": "base.enums.STATUS_CHOICES",
    },
    "SWAGGER_UI_SETTINGS": {
        "defaultModelsExpandDepth": -1,
        "displayRequestDuration": True,
        "filter": True,
        "requestSnippetsEnabled": True,
        "tagsSorter": "alpha",
    },
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

DEFAULT_LANGUAGE = os.environ.get("DEFAULT_LANGUAGE", "hi-IN")

STT_PROVIDER = os.environ.get("STT_PROVIDER", "gnani")
TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "gnani")

GNANI_API_KEY = os.environ.get("GNANI_API_KEY", "")
GNANI_BASE_URL = os.environ.get("GNANI_BASE_URL", "https://api.vachana.ai").rstrip("/")
GNANI_STT_MODEL = os.environ.get("GNANI_STT_MODEL", "prisma-v3.0")
# transcribe: digits and ₹ for numbers, English words in Latin, Hindi in Devanagari.
GNANI_STT_FORMAT = os.environ.get("GNANI_STT_FORMAT", "transcribe")
GNANI_TTS_MODEL = os.environ.get("GNANI_TTS_MODEL", "timbre-v2.5")
GNANI_TTS_VOICE = os.environ.get("GNANI_TTS_VOICE", "Poorvi")
# hi-IN, not hi-en: our replies are Latin-script Hinglish, and only an explicit Hindi language makes
# Timbre read their numbers in Hindi ("ek hazaar do sau pachaas") instead of English.
GNANI_TTS_LANGUAGE = os.environ.get("GNANI_TTS_LANGUAGE", "hi-IN")

LLM_PROVIDER = os.environ.get("LLM_PROVIDER") or "groq"
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL") or "openai/gpt-oss-120b"
# Any OpenAI-compatible chat completions base URL.
LLM_BASE_URL = (os.environ.get("LLM_BASE_URL") or "https://api.groq.com/openai/v1").rstrip("/")

MAX_AUDIO_SECONDS = float(os.environ.get("MAX_AUDIO_SECONDS", "20"))
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
LEDGER_AMOUNT_CAP = int(os.environ.get("LEDGER_AMOUNT_CAP", "50000"))
CLARIFICATION_TTL_SECONDS = 120
RESOLVER_AUTO_SCORE = float(os.environ.get("RESOLVER_AUTO_SCORE", "88"))
RESOLVER_MIN_SCORE = float(os.environ.get("RESOLVER_MIN_SCORE", "70"))
RESOLVER_MARGIN = float(os.environ.get("RESOLVER_MARGIN", "8"))
