# Production image: the source is baked in. Build on the server itself (Oracle's Ampere VMs are arm64).
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
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project

COPY . .
RUN useradd --system --no-create-home app && chown -R app /app/modules
USER app
