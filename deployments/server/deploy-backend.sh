#!/usr/bin/env bash
# Deploy backend-system (the Dagster orchestrator) onto the server VM for an env.
#   ./deploy-backend.sh <uat|prod>
#
# Ships the repo's backend-system/ to the box over SSH (tar-over-ssh — no git creds
# needed on the VM), installs Docker if missing, builds the image, and (re)runs it as a
# container on port 3000 with --network host so it reaches the PRIVATE customer360 DB
# (same subnet as the VM). Re-runnable: it rebuilds and replaces the container.
#
# SSH target + key are auto-discovered from THIS deployment's outputs (the floating IP)
# and ~/.ssh/c360-api_ed25519; override via BASTION_USER / SSH_KEY / BASTION.
# DB connection is read from the sibling ../postgres deployment.
set -euo pipefail
cd "$(dirname "$0")"                 # deployments/server
REPO_ROOT="$(cd ../.. && pwd)"       # repo root (contains backend-system/)

ENV="${1:-}"
case "$ENV" in
  uat | prod) ;;
  *) echo "Usage: ./deploy-backend.sh <uat|prod>"; exit 1 ;;
esac

[[ -f .env ]] && { set -a; source ./.env; set +a; }
SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"
tfval() { grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" 2>/dev/null | sed -E 's/.*"([^"]+)".*/\1/' | head -1; }

# --- SSH target: the BACKEND server's floating IP (selected by map key) ---
BACKEND_SERVER_KEY="${BACKEND_SERVER_KEY:-1x2}"
if [[ -z "${BASTION:-}" ]]; then
  terraform workspace select "$ENV" >/dev/null 2>&1 || { echo "ERROR: no '$ENV' server workspace — deploy the server first (./deploy.sh $ENV apply)."; exit 1; }
  SERVERS_JSON="$(terraform output -json servers 2>/dev/null || true)"
  [[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no servers output."; exit 1; }
  FIP="$(printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get("floating_ip") for i in (s.get("internal_interfaces") or []) if i.get("floating_ip")), ""))' "$BACKEND_SERVER_KEY")"
  [[ -n "$FIP" ]] || { echo "ERROR: no floating IP for server key '$BACKEND_SERVER_KEY' (set BACKEND_SERVER_KEY)."; exit 1; }
  BASTION="${BASTION_USER:-leocdp360}@$FIP"
fi
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
echo ">> Target: $BASTION"

# --- DB connection from the postgres deployment ---
pg="../postgres"
DB_NAME="$(tfval db_name "$pg/overlays/$ENV.tfvars")"
DB_USER="$(tfval db_username "$pg/overlays/$ENV.tfvars")"
DB_PASS="${TF_VAR_db_password:-$(tfval db_password "$pg/terraform.tfvars")}"
DB_HOST="$( (cd "$pg" && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -raw db_host 2>/dev/null) || true )"
DB_PORT="$( (cd "$pg" && terraform output -raw db_port 2>/dev/null) || echo 5432 )"
: "${DB_NAME:?missing db_name}"; : "${DB_USER:?missing db_username}"; : "${DB_PASS:?missing db_password}"; : "${DB_HOST:?could not read db_host from ../postgres outputs (is it applied?)}"
echo ">> DB: ${DB_NAME}@${DB_HOST}:${DB_PORT} (user ${DB_USER})"

# --- Broker Redis (on the tracking box) for the event Loader Dagster location. The Loader
#     consumes the cdp:events:raw stream produced by data-tracking-api. We pass the tracking
#     box's PRIVATE ip + the broker password (saved to ./.env by deploy-tracking.sh) and the
#     persistence gate (EVENT_LOADER_WRITE_EVENTS/_TENANT_ID, see backend-system/event_loader).
#     Absent -> the Loader idles (no broker configured) rather than failing the deploy. ---
TRACKING_SERVER_KEY="${TRACKING_SERVER_KEY:-tracking}"
SERVERS_JSON="${SERVERS_JSON:-$(terraform output -json servers 2>/dev/null || true)}"
BROKER_REDIS_HOST="$(printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get("fixed_ip") for i in (s.get("internal_interfaces") or []) if i.get("fixed_ip")), ""))' "$TRACKING_SERVER_KEY" 2>/dev/null || true)"
BROKER_REDIS_PORT="${BROKER_REDIS_PORT:-6580}"
BROKER_REDIS_PASSWORD="${BROKER_REDIS_PASSWORD:-}"   # sourced from ./.env (written by deploy-tracking.sh)
EVENT_LOADER_WRITE="${EVENT_LOADER_WRITE_EVENTS:-false}"
EVENT_LOADER_TENANT="${EVENT_LOADER_TENANT_ID:-}"
if [[ -n "$BROKER_REDIS_HOST" && -n "$BROKER_REDIS_PASSWORD" ]]; then
  echo ">> Broker: ${BROKER_REDIS_HOST}:${BROKER_REDIS_PORT} (event Loader source; write_events=$EVENT_LOADER_WRITE)"
else
  echo ">> Broker: not configured (host='${BROKER_REDIS_HOST:-}', password set=$([[ -n "$BROKER_REDIS_PASSWORD" ]] && echo yes || echo no)) — the event Loader idles until BROKER_REDIS_* are set (run deploy-tracking.sh first; it saves BROKER_REDIS_PASSWORD to ./.env)."
fi

# --- CD image source: pull the CI-built image from GHCR by default; set
#     BUILD_LOCAL=1 to fall back to shipping source + building on the VM. ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/ghcr.sh"
SERVICE="customer360-dagster"
GHCR_USER="${GHCR_USER:-${GITHUB_ACTOR:-token}}"
GHCR_TOKEN="${GHCR_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ "${BUILD_LOCAL:-0}" == "1" ]]; then
  DEPLOY_MODE="build"; IMAGE=""
  echo ">> Image: BUILD_LOCAL=1 — building $SERVICE on the VM from source."
  echo ">> Shipping backend-system/ ..."
  tar -C "$REPO_ROOT" -czf - backend-system \
    | ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo mkdir -p /opt/c360 && sudo chown "$(id -un)" /opt/c360 && tar -C /opt/c360 -xzf -'
else
  DEPLOY_MODE="ghcr"
  IMAGE="$(image_ref "$SERVICE" "$(resolve_tag "overlays/$ENV.tfvars")")"
  echo ">> Image: $IMAGE   (pull from GHCR; BUILD_LOCAL=1 to build on the VM)"
fi

# --- build + run on the VM. ALL params travel as ONE base64 blob of KEY=VALUE lines, decoded +
#     sourced on the box — this dodges ssh arg-flattening: `ssh 'bash -s' a b c` joins args into one
#     space-separated string the remote re-splits, so empty args (IMAGE/GHCR_TOKEN on a local build)
#     collapse and shift later positional args, which silently dropped the broker/loader wiring. ---
echo ">> Installing Docker (if needed), building, and (re)starting the container ..."
PW_B64="$(printf %s "$DB_PASS" | base64 | tr -d '\n')"
BROKER_PW_B64="$(printf %s "$BROKER_REDIS_PASSWORD" | base64 | tr -d '\n')"
GHCR_TOKEN_B64="$(printf %s "$GHCR_TOKEN" | base64 | tr -d '\n')"
PARAMS_B64="$(printf '%s\n' \
  "DB_HOST=$DB_HOST" "DB_PORT=$DB_PORT" "DB_NAME=$DB_NAME" "DB_USER=$DB_USER" "DB_PW_B64=$PW_B64" \
  "DEPLOY_MODE=$DEPLOY_MODE" "IMAGE=$IMAGE" "GHCR_USER=$GHCR_USER" "GHCR_TOKEN_B64=$GHCR_TOKEN_B64" \
  "BROKER_REDIS_HOST=$BROKER_REDIS_HOST" "BROKER_REDIS_PORT=$BROKER_REDIS_PORT" "BROKER_PW_B64=$BROKER_PW_B64" \
  "EVENT_LOADER_WRITE=$EVENT_LOADER_WRITE" "EVENT_LOADER_TENANT=$EVENT_LOADER_TENANT" \
  | base64 | tr -d '\n')"
ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$PARAMS_B64" <<'REMOTE'
set -euo pipefail
tmp="$(mktemp)"; printf %s "$1" | base64 -d > "$tmp"; set -a; . "$tmp"; set +a; rm -f "$tmp"
DB_PW="$(printf %s "$DB_PW_B64" | base64 -d)"
GHCR_TOKEN="$(printf %s "${GHCR_TOKEN_B64:-}" | base64 -d 2>/dev/null || true)"
BROKER_REDIS_PW="$(printf %s "${BROKER_PW_B64:-}" | base64 -d 2>/dev/null || true)"
DEPLOY_MODE="${DEPLOY_MODE:-build}"; IMAGE="${IMAGE:-}"; GHCR_USER="${GHCR_USER:-token}"
BROKER_REDIS_HOST="${BROKER_REDIS_HOST:-}"; BROKER_REDIS_PORT="${BROKER_REDIS_PORT:-6580}"
EVENT_LOADER_WRITE="${EVENT_LOADER_WRITE:-false}"; EVENT_LOADER_TENANT="${EVENT_LOADER_TENANT:-}"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  sudo systemctl enable --now docker
fi
umask 077
env_file="$(mktemp)"
cat > "$env_file" <<ENVF
DB_HOST=$DB_HOST
DB_PORT=$DB_PORT
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PW
DB_SCHEMA=$DB_NAME
ENVF
# Event Loader (backend-system/event_loader) broker connection. When the broker isn't
# configured yet, the Loader location simply idles (its sensor keeps requesting runs that
# find an empty/unreachable stream) — the rest of backend-system is unaffected.
if [ -n "$BROKER_REDIS_HOST" ] && [ -n "$BROKER_REDIS_PW" ]; then
  cat >> "$env_file" <<ENVB
BROKER_REDIS_HOST=$BROKER_REDIS_HOST
BROKER_REDIS_PORT=$BROKER_REDIS_PORT
BROKER_REDIS_PASSWORD=$BROKER_REDIS_PW
EVENT_LOADER_WRITE_EVENTS=$EVENT_LOADER_WRITE
EVENT_LOADER_TENANT_ID=$EVENT_LOADER_TENANT
ENVB
fi
sudo mkdir -p /opt/c360
sudo mv "$env_file" /opt/c360/backend.env
sudo chmod 600 /opt/c360/backend.env
if [ "$DEPLOY_MODE" = "ghcr" ]; then
  echo "   pulling $IMAGE ..."
  [ -n "$GHCR_TOKEN" ] && printf %s "$GHCR_TOKEN" | sudo docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
  sudo docker pull "$IMAGE"
  RUN_IMG="$IMAGE"
else
  echo "   building image (this can take a few minutes on a small box)..."
  sudo docker build -t customer360-dagster /opt/c360/backend-system
  RUN_IMG="customer360-dagster"
fi
sudo docker rm -f backend-system >/dev/null 2>&1 || true
sudo docker run -d --name backend-system --restart unless-stopped --network host --env-file /opt/c360/backend.env "$RUN_IMG"
sleep 3
sudo docker ps --filter name=backend-system --format '   running: {{.Names}} ({{.Status}}) image={{.Image}}'
REMOTE

echo ">> Done. Dagster UI is on the VM at :3000. Reach it from your laptop with an SSH tunnel:"
echo "   ssh -i $SSH_KEY -L 3000:localhost:3000 $BASTION   # then open http://localhost:3000"

# --- release ledger: record this deploy to the GitHub Deployments API (best-effort) ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/record_deploy.sh"
record_deployment "$ENV" "$SERVICE" "${IMAGE:-local}" success
