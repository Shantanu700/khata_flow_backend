# KhataFlow backend

KhataFlow is a voice-first shop book for Indian kirana merchants. The shopkeeper speaks in any of the 10 Indian languages Gnani supports, or Hinglish ("Sharma ji ka paanch sau udhaar likh do"), and KhataFlow records customer udhaar, stock movement and staff attendance and advances, then answers back in voice. It is built for small shop owners who keep their accounts in a paper khata today.

Built for Gnani AI's Great Indian AI Internship Challenge.

**Two repos.** This is the API (Django, voice pipeline, ledger). The web app is in
[khata_flow_frontend](https://github.com/Shantanu700/khata_flow_frontend) (Next.js). Clone both side by side:

```bash
mkdir KhataFlow && cd KhataFlow
git clone https://github.com/Shantanu700/khata_flow_backend.git backend
git clone https://github.com/Shantanu700/khata_flow_frontend.git frontend
```

## Quick start: one command

Needs Docker, [mkcert](https://github.com/FiloSottile/mkcert#installation) and [Bun](https://bun.sh).

```bash
cd backend && ./scripts/up.sh
```

It creates `.env` from `.env.example` (with a fresh `SECRET_KEY` and demo password), makes the local HTTPS
certificate, starts Postgres, Redis, the API and the live-transcript service in Docker, loads the two demo
shops, then installs and starts the web app on http://localhost:3100. Rerunning it keeps your settings and data.
`NO_FRONTEND=1` starts the API only; `FRONTEND_DIR=<path>` points at the web app elsewhere.

Set `GNANI_API_KEY` and `LLM_API_KEY` in `.env` for voice and typed commands (everything else works without them).

**Demo shops** (synthetic data: customers with udhaar, stock with low-stock items, staff with attendance and
advances, two weeks of history):

| Username | Shop |
| - | - |
| `gupta_store` | Gupta General Store |
| `patel_store` | Patel Provision Store |

Both use the password in `DEMO_PASSWORD` in `.env`; the script prints it when it finishes. It is generated on
your machine, so it is never in the repo.

## Gnani models used

| Model | What it does here | Where in the pipeline |
| - | - | - |
| **Prisma v3.0** (speech-to-text) | Turns the shopkeeper's recorded command into text. Called with the command's language plus English as the languages to expect (`ta-IN,en-IN`; an English shop sends `en-IN,hi-IN`; Hinglish uses `hi-IN`), so speech in the other language is written and tagged as what it is (the live transcript stream takes only one); `format=transcribe` (digits and ₹ for numbers), and the shop's party and product names as `keyterms`. | Stage 2, `modules/voice/providers/gnani.py` → `GnaniPrismaSTT` |
| **Timbre v3.0** (text-to-speech) | Speaks every reply, including errors and clarification questions, in the voice `Shivani` (each Timbre model has its own voices) and the command's language, so a Tamil command is answered in Tamil. Hinglish replies use `hi-IN`, so amounts are read in Hindi ("ek hazaar do sau pachaas rupaye"); with `hi-en` Timbre read them in English. | Stage 8, `modules/voice/providers/gnani.py` → `GnaniTimbreTTS` |

Both use Gnani's non-streaming REST endpoints (`POST /stt/v3`, `POST /api/v1/tts/inference`). Intent extraction uses `openai/gpt-oss-120b` on Groq, because Gnani's own LLM is not on the Gnani API platform.

## Architecture

```mermaid
flowchart LR
    A[Browser audio<br/>webm / mp4 / wav] --> B[Normalise<br/>ffmpeg → 16 kHz mono WAV, ≤ 20 s]
    T[Typed text<br/>demo backup] --> D
    B --> C[Gnani Prisma<br/>speech-to-text]
    C --> D[Intent extraction<br/>LLM, strict JSON schema]
    D --> E[Resolver<br/>rapidfuzz on names + aliases]
    E -->|unsure| Q[Clarifying question<br/>Sharma ji ya Verma ji?]
    Q -->|shopkeeper answers| E
    E --> F[Ledger<br/>atomic, append-only]
    F --> G[Reply template<br/>deterministic, per language]
    G --> H[Gnani Timbre<br/>text-to-speech]
```

Speak is a chat. Each message (a `VoiceCommand`) belongs to a `Chat`, and the server, not the screen, decides whether it answers the question the chat's previous message asked: a yes or no, a missing detail, a change at the read-back, or which of two names. The intent model reads the latest message with the chat's last 6 exchanges, so "8000" after "What is Raju's salary?" adds Raju with that salary. A question stays open for 10 minutes. Commands made before chats are filed into one chat per day by `group_old_commands`, which runs at start-up.

Every stage records its time in `VoiceCommand.timings_ms` and moves `stage` forward (`RECEIVED` → `TRANSCRIBED` → `PARSED` → `COMMITTED` / `ANSWERED` / `NEEDS_CLARIFICATION` / `FAILED`), so a failed command shows exactly where it stopped. Any provider or business-rule error becomes a polite spoken reply ("Maaf kijiye, samajh nahi aaya. Dobara boliye."), never a 500.

## Design decisions

- **Append-only ledger with reversals.** `ledger.Entry` rows are never updated or deleted. "Undo" writes a `REVERSAL` entry that points at the original, so the history of every mistake and correction stays visible. A database check constraint enforces which fields each entry kind needs.
- **Derived balances.** Customer balances, stock levels and staff dues are always computed from entries, never stored, so they cannot drift out of sync. Reversed entries and reversals are excluded in one place (`live_entries()`).
- **Deterministic response templates.** The LLM only proposes a structured command. Replies come from fixed per-language templates (`modules/voice/responses.py`, wording in `modules/voice/replies/<code>.json`), so they are fast, testable and never invent a number.
- **Clarification instead of guessing.** If a name is ambiguous ("Varma ji" sits between Verma ji and Sharma ji), unknown, or an amount is unusually large, KhataFlow asks a yes/no or either/or question and waits up to 2 minutes for the answer. Nothing is written until it is sure.
- **Provider interfaces with fakes.** Speech-to-text, text-to-speech and the intent parser sit behind small interfaces. Tests and the text-command path run on fakes, so the test suite never calls Gnani or the LLM.

## Code conventions

Same `base/` layer as the owner's certifier project:

- **Models** inherit `base.model.ModelBase`: `uuid`, `status` (`CREATED` / `UPDATED` / `DELETED`), `created_at`, `updated_at`. Deletion is soft (`soft_delete()`); the default manager hides deleted rows, and uniqueness rules only count rows that aren't deleted. Ledger entries stay `CREATED` forever.
- **Views** subclass `base.views.BaseAV` (or `BaseViewSet` for CRUD). Handlers can return a dict or pydantic model; the shared 400/401/403/409 error responses are added to the OpenAPI schema automatically. `authentication = False` (or a per-method dict) makes an endpoint public.
- **Serializers** subclass `base.serializers.BaseSerializer`: per-call `fields=` / `exclude=` and `dynamic_keys` such as `"party.name"` → `party_name`.
- Business logic lives in `services.py` / `pipeline.py`, not in views.

## Measured numbers

Only numbers from real runs, measured on 9 November 2026 on a laptop running the Docker stack, with Groq `openai/gpt-oss-120b` at `reasoning_effort=medium`.

- **Since chats (11 November 2026): 59/61 (96.7%)** on the golden set, now 61 cases, and **7/7 on the cases inside a chat**: a reply such as "aath hazaar" or "nahi, 3000 kar do" after Khata's question, "uska 200 aur likh do" after a balance, and a new instruction mid-question that must ignore it. Both misses are harmless: "Fortune Oil" without its "1L" (the resolver still finds the product) and an extra `period` of this month on a salary question. Median intent latency was 1,710 ms over the 61 calls, paced 13 seconds apart for Groq's free tier.
- **Before chats: 35/36 (97.2%)** on the golden set (`modules/voice/tests/golden_commands.yaml`, run with `scripts/eval_intents.py`), in each of the last two runs. These are 36 synthetic transcripts covering every intent, in Devanagari and romanised Hinglish, with Hindi number words and messy phrasing. The one miss differed between runs and was harmless both times: an extra `date_hint` of today, then an extra `product_name` on an udhaar command (neither changes what gets written). The set is small and written by us, so treat it as a regression check, not a benchmark.
- With `reasoning_effort=low` the golden set scored 36/36 and was about 250 ms faster, but in real spoken commands the model garbled some names ("Rathore ji" became "Rathoth ji"), so `medium` is the default (`LLM_REASONING_EFFORT`).
- **Median latency per stage**, from 7 spoken commands across both demo shops (synthetic Timbre audio sent through `POST /voice/commands/`; all 7 answered correctly):

  | Stage | Median |
  | - | - |
  | Normalise audio (ffmpeg) | 83 ms |
  | Gnani Prisma speech-to-text | 457 ms |
  | Intent extraction (Groq) | 950 ms |
  | Resolve + ledger write or query | ~10 ms |
  | Gnani Timbre text-to-speech | 986 ms |
  | **Whole request** | **2,841 ms** |

  Across the 36 golden-set calls, the median intent latency was 1,140 ms.

- Groq's free tier allows 8,000 tokens a minute and each command uses about 1,450, so roughly 5 commands a minute. On a rate limit the parser waits up to 10 seconds and retries once. If Groq's strict JSON mode fails on a reply, the parser retries once without it and validates the result itself.

## Run it yourself, without the script

`./scripts/up.sh` runs exactly these steps. Do them by hand to see each part, or to run the API without Docker.
Commands start in `backend/` unless a step says otherwise.

### 1. Settings and the local HTTPS certificate (both ways)

Everything runs on HTTPS, including localhost, with a certificate that only your machine trusts.

```bash
cp .env.example .env
```

Then fill in `.env`:

| Key | What to put |
| - | - |
| `SECRET_KEY` | any long random string: `python3 -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `DEMO_PASSWORD` | the password the two demo shops will get |
| `GNANI_API_KEY` | from https://app.gnani.ai; needed for speech-to-text and voice replies |
| `LLM_API_KEY` | from https://console.groq.com/keys; needed for voice and typed commands |

Without the two API keys everything except voice and typed commands still works.

```bash
mkdir -p docker/nonprod/certs                      # git-ignored, so a fresh clone doesn't have it
mkcert -install                                    # once per machine: trust mkcert's local CA
mkcert -cert-file docker/nonprod/certs/localhost.pem -key-file docker/nonprod/certs/localhost-key.pem \
       localhost 127.0.0.1 ::1
```

### 2a. The API in Docker (recommended)

Needs Docker.

```bash
export COMPOSE_FILE=docker/nonprod/docker-compose.yml   # or pass -f with every compose command
docker compose up -d                                   # Postgres, Redis, migrations, API, live transcript, worker
docker compose ps                                      # wait until web and stream are running
docker compose exec web python manage.py seed_demo     # the two demo shops (safe to rerun)
```

- The API is on https://localhost:8000 and the live transcript on wss://localhost:8001.
- Leave `DATABASE_URL` and `REDIS_URL` unset in `.env`; the defaults point at the compose services.
- Stop it with `docker compose down`, which keeps the data. `docker compose down -v` also deletes the database.

### 2b. The API on your machine, without Docker

Needs Python 3.13, [uv](https://docs.astral.sh/uv/), ffmpeg, and Postgres 16 or newer.

- **ffmpeg** is for spoken commands only. Browsers record WebM/Opus (Safari: MP4) and Gnani's speech-to-text takes
  16 kHz mono WAV, so the API converts each recording. Without it, spoken commands fail with `FFMPEG_MISSING`;
  everything else works. `brew install ffmpeg`, or `apt install ffmpeg`. The Docker image already has it.
- **Postgres:** an empty database and a user that owns it.
- **Redis is not needed.** It only feeds the Celery worker, which has no jobs yet.

Add the database to `.env`, for example:

```bash
DATABASE_URL=postgres://khaataflow:khaataflow@localhost:5432/khaataflow
```

```bash
uv sync                                                # creates .venv with the API and dev tools
uv run python manage.py makemigrations shops ledger voice   # migrations are generated, not committed
uv run python manage.py migrate
uv run python manage.py group_old_commands             # files commands made before chats into chats (once)
uv run python manage.py seed_demo
```

Then start two processes, each in its own terminal:

```bash
# The API on https://localhost:8000
uv run python manage.py runsslserver 0.0.0.0:8000 \
  --certificate docker/nonprod/certs/localhost.pem --key docker/nonprod/certs/localhost-key.pem
```

```bash
# The live transcript on wss://localhost:8001; without it, speaking still works but no words show while you talk
uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8001 --lifespan off --no-access-log \
  --ssl-certfile docker/nonprod/certs/localhost.pem --ssl-keyfile docker/nonprod/certs/localhost-key.pem
```

### 3. The web app

Needs [Bun](https://bun.sh). In a new terminal:

```bash
cd ../frontend
cp .env.example .env.local      # KHATAFLOW_API_URL=https://localhost:8000
bun install
bun run dev:api                 # http://localhost:3100
```

`dev:api` hands mkcert's CA to Node so the web app's server can call the API over HTTPS.

Open http://localhost:3100 and sign in as `gupta_store` or `patel_store` with your `DEMO_PASSWORD`.

### Useful extras

```bash
docker compose exec web python manage.py drf_create_token gupta_store   # show a merchant's API token
docker compose exec web python manage.py createsuperuser                # for /admin/
docker compose exec web python manage.py gnani_smoke_test               # one real Prisma + Timbre call each
```

Without Docker, use `uv run python manage.py …` in place of `docker compose exec web python manage.py …`.

Edits to `.env` apply without restarting anything: Django reads the file itself, the dev server reloads when it changes, and the Celery worker runs under `watchfiles`.

Migrations are generated by the `init` service on start-up and are not committed, the same as the owner's other projects.

The dev server is `runsslserver` (django-sslserver-v2) with a locally trusted mkcert certificate, so everything is on `https://localhost:8000` and plain `http://` is refused. The live transcript is the one async piece: a WebSocket (`modules/voice/stream.py`, served by uvicorn as the `stream` service on `wss://localhost:8001`) that relays the browser's microphone to Gnani's streaming speech-to-text and sends each phrase back while the shopkeeper speaks. The web app's server calls the API with `NODE_EXTRA_CA_CERTS="$(mkcert -CAROOT)/rootCA.pem"`, keeping TLS verification on.

- API docs: https://localhost:8000/api/schema/swagger-ui/ (Swagger) and https://localhost:8000/api/schema/redoc/ (ReDoc)
- Admin: https://localhost:8000/admin/
- Tests: `docker compose exec web pytest`
- Lint: `uv run ruff check .`
- Intent eval: `docker compose exec web python scripts/eval_intents.py`

## API

All endpoints are under `/api/v1/` and only ever see the caller's own shop. Everything except sign-in, register and the language catalogs needs `Authorization: Token <token>`. Errors look like `{"error": {"code": "...", "message": "..."}}`. Updates are always `PUT` with the full object; `PATCH` is not accepted anywhere (405).

```bash
API=https://localhost:8000/api/v1

# Sign in (10 tries a minute), register, sign out, the shop's settings
curl -H "Content-Type: application/json" -d '{"username": "gupta_store", "password": "..."}' $API/auth/login/   # → token, shop
curl -H "Content-Type: application/json" \
     -d '{"shop_name": "...", "username": "...", "password": "...", "preferred_language": "ta-IN"}' $API/auth/register/
TOKEN=<the token from sign-in or seed_demo>
curl -H "Authorization: Token $TOKEN" -X POST $API/auth/logout/
curl -H "Authorization: Token $TOKEN" -X PUT -H "Content-Type: application/json" \
     -d '{"preferred_language": "bn-IN"}' $API/me/                        # also GET

# A spoken message (multipart audio, any format the browser records); the language defaults to the shop's.
# Without "chat" a new chat starts; the reply carries its id.
curl -H "Authorization: Token $TOKEN" -F audio=@command.webm -F language=hi-IN -F chat=7 $API/voice/commands/

# A typed message: the same pipeline without speech-to-text
curl -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
     -d '{"text": "Naya staff Raju jodo", "chat": 7}' $API/voice/text-commands/
# The next message in the same chat answers Khata's question: "aath hazaar", "haan", "nahi, 9000", "Verma ji wale"
curl -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
     -d '{"text": "aath hazaar", "chat": 7}' $API/voice/text-commands/

# A question can also be answered by its option id, or yes/no
curl -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
     -d '{"option_id": "party:2"}' $API/voice/commands/42/clarify/

curl -H "Authorization: Token $TOKEN" "$API/voice/chats/?page=1"              # chats, most recently active first
curl -H "Authorization: Token $TOKEN" "$API/voice/chats/7/messages/?page=1"   # a chat's messages, newest first
curl -H "Authorization: Token $TOKEN" $API/voice/commands/42/audio/     # reply audio, owner only
curl -H "Authorization: Token $TOKEN" -X POST $API/voice/stream-ticket/  # 30-second ticket for the live transcript
# then wss://localhost:8001/api/v1/voice/stream/?ticket=…&language=hi-IN : send 1,024-byte frames of 16 kHz 16-bit
# mono PCM, "commit" to finish the phrase; each message back is {"text": "…"}

# Data: every list takes page and page_size, plus q (search) and ordering
curl -H "Authorization: Token $TOKEN" "$API/parties/?kind=CUSTOMER&q=sharma&balance=owing&ordering=-balance"
curl -H "Authorization: Token $TOKEN" "$API/parties/?kind=STAFF&advance=yes&month=2026-11"   # with staff_due
curl -H "Authorization: Token $TOKEN" "$API/parties/3/month/?month=2026-10"   # staff due and days present
curl -H "Authorization: Token $TOKEN" "$API/products/?low=true&ordering=stock_level"
curl -H "Authorization: Token $TOKEN" "$API/entries/?kind=CREDIT_GIVEN,PAYMENT_RECEIVED&party=2&date_from=2026-11-01"
                              # filtered to one customer, rows carry running_balance
curl -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
     -d '{"kind": "PAYMENT_RECEIVED", "party": 2, "amount": "200"}' $API/entries/   # 409 if more than the balance
curl -H "Authorization: Token $TOKEN" -X POST $API/entries/17/undo/
curl -H "Authorization: Token $TOKEN" $API/summary/                     # dashboard: totals, 14 days, recent

# Interface text for the web app (no sign-in needed)
curl $API/i18n/              # the 11 languages with native names
curl $API/i18n/ta-IN/        # one language's strings
```

A voice or text command returns `id`, `stage` (where the pipeline got to), `status` (the record's lifecycle, like every resource), `transcript`, `parsed`, `response_text`, `response_audio_url`, `entry` (if one was written), `clarification` (if a question is pending), `error_code` and `timings_ms`.

## Data note

All demo data is synthetic: the two shops (`gupta_store` → Gupta General Store, `patel_store` → Patel Provision Store), customers, staff, products and entries are made up, and no party has a phone number or any other contact detail. No real recordings are used; the smoke test transcribes Timbre's own synthetic speech unless you pass your own recording with `--audio`.

## Languages

The web app and voice replies come in Hinglish (`hi-Latn`, the original wording) and the 10 Gnani languages. Interface text is in `modules/i18n/catalogs/<code>.json` and spoken replies in `modules/voice/replies/<code>.json`; a test checks every file has the same keys and placeholders as the Hinglish one. **The translations other than Hinglish and English are machine-drafted and need review by native speakers before real shopkeepers use them.**

## Known limits

- Recordings and reply audio are never served from `/media/`. Reply audio is only available to the shop that owns it, through `/voice/commands/<id>/audio/`.
- One owner per shop; no roles.
