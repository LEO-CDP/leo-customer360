#!/usr/bin/env bash
# Deploy customer360-api (FastAPI) onto the "api" server VM for an env.
#   ./deploy-api.sh <uat|prod>
#
# Ships the repo's customer360-api/ to the box over SSH (tar-over-ssh), installs Docker if
# missing, builds the image, and (re)runs it as a container with --network host on :8008,
# wired to the PRIVATE customer360 DB and to the backend box's Dagster GraphQL (:3000).
# Re-runnable. Target box = servers["$API_SERVER_KEY"] (default "api"); the Dagster host is
# servers["$BACKEND_SERVER_KEY"] (default "1x2") private IP. Overrides: BASTION_USER/SSH_KEY.
set -euo pipefail
cd "$(dirname "$0")"                 # deployments/server
REPO_ROOT="$(cd ../.. && pwd)"       # repo root (contains customer360-api/)

ENV="${1:-}"
case "$ENV" in
  uat | prod) ;;
  *) echo "Usage: ./deploy-api.sh <uat|prod>"; exit 1 ;;
esac

[[ -f .env ]] && { set -a; source ./.env; set +a; }
SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"
API_SERVER_KEY="${API_SERVER_KEY:-api}"
BACKEND_SERVER_KEY="${BACKEND_SERVER_KEY:-1x2}"
# Read a tfvars value: content between quotes for strings (keeps '#'), or the bare
# token with any trailing comment stripped for unquoted numbers/bools (e.g. redis_port).
tfval() {
  local line; line="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" 2>/dev/null | head -1)"
  case "$line" in
    *\"*\"*) line="${line#*\"}"; printf '%s' "${line%%\"*}" ;;
    *) line="${line#*=}"; line="${line%%#*}"; printf '%s' "$(printf '%s' "$line" | tr -d '[:space:]')" ;;
  esac
}

# --- resolve server IPs by map key from THIS deployment's outputs ---
terraform workspace select "$ENV" >/dev/null 2>&1 || { echo "ERROR: no '$ENV' server workspace — deploy the server first."; exit 1; }
SERVERS_JSON="$(terraform output -json servers 2>/dev/null || true)"
[[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no servers output."; exit 1; }
# select an IP field for a given server map key; JSON on stdin (avoids Windows/MSYS temp-path issues)
srv_ip() { printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get(sys.argv[2]) for i in (s.get("internal_interfaces") or []) if i.get(sys.argv[2])), ""))' "$1" "$2"; }
API_FIP="$(srv_ip "$API_SERVER_KEY" floating_ip)"
DAG_HOST="$(srv_ip "$BACKEND_SERVER_KEY" fixed_ip)"
[[ -n "$API_FIP" ]] || { echo "ERROR: no floating IP for server key '$API_SERVER_KEY'."; exit 1; }
BASTION="${BASTION_USER:-leocdp360}@$API_FIP"
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
echo ">> Target (api): $BASTION   Dagster host (backend): ${DAG_HOST:-<none>}:3000"

# --- DB connection from the postgres deployment ---
pg="../postgres"
DB_NAME="$(tfval db_name "$pg/overlays/$ENV.tfvars")"
DB_USER="$(tfval db_username "$pg/overlays/$ENV.tfvars")"
DB_PASS="${TF_VAR_db_password:-$(tfval db_password "$pg/terraform.tfvars")}"
DB_HOST="$( (cd "$pg" && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -raw db_host 2>/dev/null) || true )"
DB_PORT="$( (cd "$pg" && terraform output -raw db_port 2>/dev/null) || echo 5432 )"
: "${DB_NAME:?missing db_name}"; : "${DB_USER:?missing db_username}"; : "${DB_PASS:?missing db_password}"; : "${DB_HOST:?could not read db_host from ../postgres outputs}"
echo ">> DB: ${DB_NAME}@${DB_HOST}:${DB_PORT} (user ${DB_USER})"

# --- Redis cache (optional) from the sibling ../cache deployment. Absent -> caching
#     stays DISABLED (the API fails open). uat: co-located container; prod: managed MemStore. ---
cache="../cache"
REDIS_PASS="${TF_VAR_redis_password:-$(tfval redis_password "$cache/terraform.tfvars")}"
REDIS_HOST=""; REDIS_PORT=""
if [[ -n "$REDIS_PASS" ]]; then
  if [[ "$ENV" == "uat" ]]; then
    REDIS_HOST="127.0.0.1" # Redis container runs on THIS box (--network host)
    REDIS_PORT="$(tfval redis_port "$cache/overlays/uat.tfvars")"; REDIS_PORT="${REDIS_PORT:-6580}"
  else
    REDIS_HOST="$( (cd "$cache" && terraform workspace select prod >/dev/null 2>&1 && terraform output -raw redis_host 2>/dev/null) || true )"
    REDIS_PORT="$( (cd "$cache" && terraform output -raw redis_port 2>/dev/null) || true )"; REDIS_PORT="${REDIS_PORT:-6379}"
  fi
fi
if [[ -n "$REDIS_HOST" ]]; then echo ">> Redis: ${REDIS_HOST}:${REDIS_PORT} (cache enabled)"; else echo ">> Redis: not configured — caching DISABLED (fail-open)."; fi

# --- ship customer360-api/ to the VM ---
echo ">> Shipping customer360-api/ ..."
tar -C "$REPO_ROOT" -czf - customer360-api \
  | ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo mkdir -p /opt/c360 && sudo chown "$(id -un)" /opt/c360 && tar -C /opt/c360 -xzf -'

# --- build + run on the VM (values passed as positional args; password base64'd) ---
echo ">> Installing Docker (if needed), building, and (re)starting the container ..."
PW_B64="$(printf %s "$DB_PASS" | base64 | tr -d '\n')"
REDIS_PW_B64="$(printf %s "${REDIS_PASS:-}" | base64 | tr -d '\n')"
ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$DB_HOST" "$DB_PORT" "$DB_NAME" "$DB_USER" "$PW_B64" "${DAG_HOST:-127.0.0.1}" "${REDIS_HOST:-}" "${REDIS_PORT:-}" "$REDIS_PW_B64" <<'REMOTE'
set -euo pipefail
DB_HOST="$1"; DB_PORT="$2"; DB_NAME="$3"; DB_USER="$4"; DB_PW="$(printf %s "$5" | base64 -d)"; DAG_HOST="$6"
REDIS_HOST="$7"; REDIS_PORT="$8"; REDIS_PW="$(printf %s "${9:-}" | base64 -d 2>/dev/null || true)"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  sudo systemctl enable --now docker
fi
umask 077
env_file="$(mktemp)"
cat > "$env_file" <<ENVF
ENVIRONMENT=production
DB_HOST=$DB_HOST
DB_PORT=$DB_PORT
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PW
DB_SCHEMA=$DB_NAME
DAGSTER_GRAPHQL_HOST=$DAG_HOST
DAGSTER_GRAPHQL_PORT=3000
ENVF
if [[ -n "$REDIS_HOST" && -n "$REDIS_PW" ]]; then
  cat >> "$env_file" <<ENVR
REDIS_HOST=$REDIS_HOST
REDIS_PORT=${REDIS_PORT:-6580}
REDIS_PASSWORD=$REDIS_PW
REDIS_DB=0
CACHE_ENABLED=true
ENVR
else
  echo "CACHE_ENABLED=false" >> "$env_file"
fi
sudo mkdir -p /opt/c360
sudo mv "$env_file" /opt/c360/api.env
sudo chmod 600 /opt/c360/api.env
echo "   building image (a few minutes on a small box)..."
# The Dockerfile uses `RUN --mount=type=cache` (BuildKit-only) but docker.io ships no
# buildx, so strip the mount (it's only a pip-cache optimization) and use the classic builder.
sed -i 's/ --mount=[^ ]*//g' /opt/c360/customer360-api/Dockerfile
sudo docker build -t customer360-api /opt/c360/customer360-api
sudo docker rm -f customer360-api >/dev/null 2>&1 || true
sudo docker run -d --name customer360-api --restart unless-stopped --network host --env-file /opt/c360/api.env customer360-api
sleep 3
sudo docker ps --filter name=customer360-api --format '   running: {{.Names}} ({{.Status}}) image={{.Image}}'
REMOTE

echo ">> Done. customer360-api is on the VM at :8008 (health: /health). Reach it via an SSH tunnel:"
echo "   ssh -i $SSH_KEY -L 8008:localhost:8008 $BASTION   # then open http://localhost:8008/health"
