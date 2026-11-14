# Production image (Render), with the source baked in.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /bin/

# ffmpeg converts browser recordings (webm/opus) to the WAV that Prisma accepts.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .
RUN useradd --system --no-create-home app && chown -R app /app/modules
USER app

# Migrations are generated, not committed, so every start generates them before migrating (see docker/prod/README.md).
# --forwarded-allow-ips: only Render's proxy reaches this port, so its X-Forwarded-Proto is trusted.
# No access log: the live-transcript ticket travels in the WebSocket URL.
CMD ["sh", "-c", "python manage.py makemigrations shops ledger voice && python manage.py migrate --noinput && exec uvicorn config.asgi:application --host 0.0.0.0 --port ${PORT:-10000} --lifespan off --no-access-log --proxy-headers --forwarded-allow-ips '*'"]
