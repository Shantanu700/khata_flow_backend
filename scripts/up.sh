#!/usr/bin/env bash
# The whole KhaataFlow stack in one command: the API in Docker with the two demo shops, then the web app.
#
#   ./scripts/up.sh                       # from this repo; expects the frontend repo at ../frontend
#   FRONTEND_DIR=~/code/khata_flow_frontend ./scripts/up.sh
#   NO_FRONTEND=1 ./scripts/up.sh         # API only
#
# Safe to rerun: it keeps an existing .env, certificates, database and demo data.
set -euo pipefail
cd "$(dirname "$0")/.."
FRONTEND_DIR="${FRONTEND_DIR:-../frontend}"
export COMPOSE_FILE=docker/nonprod/docker-compose.yml

need() { command -v "$1" >/dev/null 2>&1 || { echo "KhaataFlow needs $1: $2" >&2; exit 1; }; }
random() { openssl rand -base64 64 | tr -dc 'A-Za-z0-9' | cut -c "1-$1"; }
env_value() { sed -n "s/^$1=//p" .env | tail -1; }
set_env() {
  if grep -q "^$1=" .env; then
    awk -v k="$1" -v v="$2" 'index($0, k "=") == 1 { print k "=" v; next } { print }' .env > .env.tmp && mv .env.tmp .env
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}

need docker "https://docs.docker.com/get-docker/"
need mkcert "https://github.com/FiloSottile/mkcert#installation (brew install mkcert on macOS)"
need openssl "your package manager"

echo "1/4  Settings"
if [ ! -f .env ]; then
  cp .env.example .env
  set_env SECRET_KEY "$(random 50)"
  echo "     created .env from .env.example"
fi
[ -n "$(env_value DEMO_PASSWORD)" ] || set_env DEMO_PASSWORD "$(random 14)"
for key in GNANI_API_KEY LLM_API_KEY; do
  [ -n "$(env_value "$key")" ] || echo "     ! $key is empty in .env: voice and typed commands won't work until it is set"
done

echo "2/4  HTTPS certificate (mkcert, trusted on this machine only)"
if [ ! -f docker/nonprod/certs/localhost.pem ]; then
  mkdir -p docker/nonprod/certs  # git-ignored, so a fresh clone has no such folder
  mkcert -install
  mkcert -cert-file docker/nonprod/certs/localhost.pem -key-file docker/nonprod/certs/localhost-key.pem \
    localhost 127.0.0.1 ::1
fi

echo "3/4  API, database and demo data (Docker)"
docker compose up -d
CA="$(mkcert -CAROOT)/rootCA.pem"
for _ in $(seq 1 90); do
  curl -sf --cacert "$CA" -o /dev/null https://localhost:8000/api/v1/i18n/ && break
  sleep 2
done
curl -sf --cacert "$CA" -o /dev/null https://localhost:8000/api/v1/i18n/ \
  || { echo "The API did not come up; see: docker compose -f $COMPOSE_FILE logs web" >&2; exit 1; }
# API tokens are left out of the output; show one with: docker compose exec web python manage.py drf_create_token gupta_store
docker compose exec -T web python manage.py seed_demo | grep -v "API token" | sed 's/^/     /'

cat <<EOF

     Demo sign-in (both shops use the DEMO_PASSWORD in backend/.env):
       gupta_store   Gupta General Store
       patel_store   Patel Provision Store
       password      $(env_value DEMO_PASSWORD)
     (Accounts made by an earlier run keep the password they were created with.)

     API  https://localhost:8000/api/schema/swagger-ui/

EOF

if [ -n "${NO_FRONTEND:-}" ]; then exit 0; fi
if [ ! -d "$FRONTEND_DIR" ]; then
  echo "4/4  Web app skipped: no frontend repo at $FRONTEND_DIR."
  echo "     git clone https://github.com/Shantanu700/khata_flow_frontend.git $FRONTEND_DIR, then rerun."
  exit 0
fi
echo "4/4  Web app on http://localhost:3100"
need bun "https://bun.sh"
cd "$FRONTEND_DIR"
[ -f .env.local ] || cp .env.example .env.local
bun install --frozen-lockfile
if lsof -nP -iTCP:3100 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "     already running on http://localhost:3100"
  exit 0
fi
echo "     Ctrl+C stops the web app; the API keeps running (docker compose -f $COMPOSE_FILE down to stop it)."
exec bun run dev:api
