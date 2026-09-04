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

# --- Object storage (vStorage / S3) from the ../storage deployment. OPTIONAL:
#     if unset, the container's renderer falls back to local compute logs. The
#     first bucket for this env holds Dagster compute logs (under a prefix). ---
store="../storage"
S3_ENDPOINT="$(tfval s3_endpoint "$store/overlays/$ENV.tfvars")"
S3_REGION="$(tfval region "$store/overlays/$ENV.tfvars")"; S3_REGION="${S3_REGION:-us-east-1}"
S3_BUCKET="$(tfval bucket_names "$store/overlays/$ENV.tfvars")"   # first quoted bucket name
S3_ACCESS_KEY="${TF_VAR_access_key:-$(tfval access_key "$store/terraform.tfvars")}"
S3_SECRET_KEY="${TF_VAR_secret_key:-$(tfval secret_key "$store/terraform.tfvars")}"
if [[ -n "$S3_ENDPOINT" && -n "$S3_BUCKET" && -n "$S3_ACCESS_KEY" && -n "$S3_SECRET_KEY" ]]; then
  echo ">> S3: $S3_ENDPOINT bucket=$S3_BUCKET (region $S3_REGION, path-style) — compute logs -> vStorage"
else
  echo ">> S3: not fully configured — compute logs will use the local default"
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

# --- build + run on the VM (values passed as positional args; password base64'd) ---
echo ">> Installing Docker (if needed), building, and (re)starting the container ..."
# The full backend.env is passed as ONE base64 blob ($1, always non-empty), so it
# is immune to ssh flattening dropping empty positional args (which shifts every
# later param). Only IMAGE + the GHCR token — which may be empty in BUILD_LOCAL /
# no-token mode — are passed as the LAST positional args, where a collapse is
# harmless (they are unused in build mode and default to empty otherwise).
ENV_CONTENT="$(cat <<ENVBODY
DB_HOST=$DB_HOST
DB_PORT=$DB_PORT
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASS
DB_SCHEMA=$DB_NAME
S3_ENDPOINT=$S3_ENDPOINT
S3_ENDPOINT_URL=$S3_ENDPOINT
S3_REGION=$S3_REGION
S3_FORCE_PATH_STYLE=true
MINIO_BUCKET=$S3_BUCKET
AWS_ACCESS_KEY_ID=$S3_ACCESS_KEY
AWS_SECRET_ACCESS_KEY=$S3_SECRET_KEY
S3_ACCESS_KEY_ID=$S3_ACCESS_KEY
S3_SECRET_ACCESS_KEY=$S3_SECRET_KEY
ENVBODY
)"
ENV_B64="$(printf %s "$ENV_CONTENT" | base64 | tr -d '\n')"
ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$ENV_B64" "$DEPLOY_MODE" "$GHCR_USER" "$IMAGE" "$(printf %s "$GHCR_TOKEN" | base64 | tr -d '\n')" <<'REMOTE'
set -euo pipefail
ENV_B64="$1"; DEPLOY_MODE="$2"; GHCR_USER="${3:-token}"; IMAGE="${4:-}"; GHCR_TOKEN="$(printf %s "${5:-}" | base64 -d 2>/dev/null || true)"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  sudo systemctl enable --now docker
fi
# Reclaim disk before we write/pull anything. Each deploy pulls a new SHA-pinned image
# and the old ones pile up until a small VM fills its disk ("No space left on device"
# on the very first env-file write). This runs before any disk write (the heredoc streams
# over stdin) so it recovers even from an already-full disk. The currently-running
# backend-system still holds its image here, so `image prune -a` keeps it and drops only
# the stale ones. Best-effort: never fail the deploy on cleanup.
if command -v docker >/dev/null 2>&1; then
  echo "   reclaiming disk (df before): $(df -h --output=avail / | tail -1 | tr -d ' ') free"
  sudo docker container prune -f  >/dev/null 2>&1 || true
  sudo docker image prune -a -f   >/dev/null 2>&1 || true
  sudo docker builder prune -a -f >/dev/null 2>&1 || true
  echo "   reclaiming disk (df after):  $(df -h --output=avail / | tail -1 | tr -d ' ') free"
fi
umask 077
env_file="$(mktemp)"
printf %s "$ENV_B64" | base64 -d > "$env_file"
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
# Preserve the OLD instance's Dagster storage before replacing the container. The
# old container ran with an EPHEMERAL DAGSTER_HOME (no -v mount), so its SQLite
# run/event/schedule history lives ONLY inside the container layer — copy it out
# now or `docker rm` destroys it. Import later with
# backend-system/scripts/migrate_dagster_sqlite_to_postgres.py (see deployment.md).
if sudo docker ps -a --format '{{.Names}}' | grep -qx backend-system; then
  ts="$(date -u +%Y%m%d-%H%M%S)"; bak="/opt/c360/dagster-home-backup-$ts.tar"
  echo "   backing up old DAGSTER_HOME -> $bak"
  if sudo docker cp backend-system:/dagster_home - > "$bak" 2>/dev/null; then
    echo "   backup saved ($(du -h "$bak" | cut -f1))"
  else
    rm -f "$bak"; echo "   (nothing to back up, or copy failed — continuing)"
  fi
fi
# NOTE: the container's entrypoint (render_dagster_instance.py) ensures the
# dedicated `dagster` database exists and picks storage adaptively — shared
# PostgreSQL if reachable, else local SQLite — so the deploy does NOT hard-depend
# on Postgres being up. Nothing to do here.
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
