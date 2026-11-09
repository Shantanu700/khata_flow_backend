# Dependencies only: compose mounts the source at /app, so edits apply without a rebuild.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /uvx /bin/

# ffmpeg converts browser recordings (webm/opus) to the WAV that Prisma accepts.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project
