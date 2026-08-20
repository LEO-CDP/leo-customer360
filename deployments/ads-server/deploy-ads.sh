#!/usr/bin/env bash
# Deploy ads-server (LEO Ad Server, FastAPI :9009) onto a VM and bootstrap its
# leo_ads schema in the shared customer360 database.
#
#   uat  -> container on the api box (shared; server key "api"). It reuses the
#           co-located Redis (127.0.0.1:6580) and the private managed Postgres.
#   prod -> container on a DEDICATED vServer (server key "ads").
#
#   ./deploy-ads.sh <uat|prod>            # (re)deploy + ensure schema
#   ./deploy-ads.sh <uat|prod> destroy    # remove the container
#
# The leo_ads schema (sql-scripts/db-schema-init.sql) is created idempotently on
# every deploy. Sample data (sql-scripts/sample-data-init.sql) is loaded only when
# ads_seed_sample=true in the overlay. leo_ads has no RLS, so no tenant context is
# needed. DB creds come from ../postgres; the Redis password from ../cache.
set -euo pipefail
cd "$(dirname "$0")"           # deployments/ads-server
REPO_ROOT="$(cd ../.. && pwd)" # repo root (contains ads-server/)

ENV="${1:-}"; ACTION="${2:-deploy}"
case "$ENV" in uat | prod) ;; *) echo "Usage: ./deploy-ads.sh <uat|prod> [deploy|destroy]"; exit 1 ;; esac

[[ -f .env ]] && { set -a; source ./.env; set +a; }
SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"
tfval() {
  local line; line="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" 2>/dev/null | head -1)"
  case "$line" in
    *\"*\"*) line="${line#*\"}"; printf '%s' "${line%%\"*}" ;;
    *) line="${line#*=}"; line="${line%%#*}"; printf '%s' "$(printf '%s' "$line" | tr -d '[:space:]')" ;;
  esac
}

ovl="overlays/${ENV}.tfvars"
[[ -f "$ovl" ]] || { echo "ERROR: overlay $ovl not found."; exit 1; }
ADS_SERVER_KEY="${ADS_SERVER_KEY:-$(tfval ads_server_key "$ovl")}"; ADS_SERVER_KEY="${ADS_SERVER_KEY:-api}"
ADS_PORT="$(tfval ads_port "$ovl")"; ADS_PORT="${ADS_PORT:-9009}"
DB_SCHEMA="$(tfval ads_db_schema "$ovl")"; DB_SCHEMA="${DB_SCHEMA:-leo_ads}"
SEED_SAMPLE="$(tfval ads_seed_sample "$ovl")"; SEED_SAMPLE="${SEED_SAMPLE:-false}"
ENVIRONMENT="$(tfval ads_environment "$ovl")"; ENVIRONMENT="${ENVIRONMENT:-production}"
ADS_ROOT_PATH="$(tfval ads_root_path "$ovl")"   # e.g. /ads when fronted by Caddy under that path (empty = served at root)

# --- resolve the target VM from ../server outputs ---
SERVERS_JSON="$( (cd ../server && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -json servers 2>/dev/null) || true )"
[[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no ../server servers output for $ENV — deploy the server first."; exit 1; }
FIP="$(printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get("floating_ip") for i in (s.get("internal_interfaces") or []) if i.get("floating_ip")), ""))' "$ADS_SERVER_KEY")"
[[ -n "$FIP" ]] || { echo "ERROR: no floating IP for server key '$ADS_SERVER_KEY' — define it in ../server/overlays/$ENV.tfvars."; exit 1; }
BASTION="${BASTION_USER:-leocdp360}@$FIP"
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)

if [[ "$ACTION" == "destroy" ]]; then
  echo ">> Removing ads-server container on $BASTION ..."
  ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo docker rm -f customer360-ads >/dev/null 2>&1; echo "   removed"'
  exit 0
fi

# --- DB connection from the postgres deployment ---
pg="../postgres"
DB_NAME="$(tfval db_name "$pg/overlays/$ENV.tfvars")"
DB_USER="$(tfval db_username "$pg/overlays/$ENV.tfvars")"
DB_PASS="${TF_VAR_db_password:-$(tfval db_password "$pg/terraform.tfvars")}"
DB_HOST="$( (cd "$pg" && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -raw db_host 2>/dev/null) || true )"
DB_PORT="$( (cd "$pg" && terraform output -raw db_port 2>/dev/null) || echo 5432 )"
: "${DB_NAME:?missing db_name}"; : "${DB_USER:?missing db_username}"; : "${DB_PASS:?missing db_password}"; : "${DB_HOST:?could not read db_host from ../postgres outputs}"

# --- Redis from the cache deployment (uat: co-located on this box; prod: managed) ---
cache="../cache"
REDIS_PASS="${TF_VAR_redis_password:-$(tfval redis_password "$cache/terraform.tfvars")}"
if [[ "$ENV" == "uat" ]]; then
  REDIS_HOST="127.0.0.1"; REDIS_PORT="$(tfval redis_port "$cache/overlays/uat.tfvars")"; REDIS_PORT="${REDIS_PORT:-6580}"
else
  REDIS_HOST="$( (cd "$cache" && terraform workspace select prod >/dev/null 2>&1 && terraform output -raw redis_host 2>/dev/null) || true )"
  REDIS_PORT="$( (cd "$cache" && terraform output -raw redis_port 2>/dev/null) || true )"; REDIS_PORT="${REDIS_PORT:-6379}"
fi

echo ">> Target (ads): $BASTION :$ADS_PORT   DB: ${DB_NAME}.${DB_SCHEMA}@${DB_HOST}:${DB_PORT}   Redis: ${REDIS_HOST:-<none>}:${REDIS_PORT:-}"

# --- CD image source: pull the CI-built image from GHCR by default; set
#     BUILD_LOCAL=1 to fall back to shipping source + building on the VM. ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/ghcr.sh"
SERVICE="ads-server"
GHCR_USER="${GHCR_USER:-${GITHUB_ACTOR:-token}}"
GHCR_TOKEN="${GHCR_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ "${BUILD_LOCAL:-0}" == "1" ]]; then
  DEPLOY_MODE="build"; IMAGE=""
  echo ">> Image: BUILD_LOCAL=1 — building $SERVICE on the VM from source."
  echo ">> Shipping ads-server/ ..."
  tar -C "$REPO_ROOT" -czf - ads-server \
    | ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo mkdir -p /opt/c360 && sudo chown "$(id -un)" /opt/c360 && tar -C /opt/c360 -xzf -'
else
  DEPLOY_MODE="ghcr"
  IMAGE="$(image_ref "$SERVICE" "$(resolve_tag "overlays/$ENV.tfvars")")"
  echo ">> Image: $IMAGE   (pull from GHCR; BUILD_LOCAL=1 to build on the VM)"
  # The leo_ads schema bootstrap (below) runs psql against ads-server/sql-scripts/
  # on the VM, so ship just those SQL files even when the image comes from GHCR.
  echo ">> Shipping ads-server/sql-scripts/ (schema bootstrap) ..."
  tar -C "$REPO_ROOT" -czf - ads-server/sql-scripts \
    | ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo mkdir -p /opt/c360 && sudo chown "$(id -un)" /opt/c360 && tar -C /opt/c360 -xzf -'
fi

# env file built locally, shipped base64 (dodges ssh arg-flattening). CACHE_ENABLED
# only when a Redis password+host is available; otherwise the app fails open.
CACHE_ENABLED="false"; [[ -n "${REDIS_HOST:-}" && -n "${REDIS_PASS:-}" ]] && CACHE_ENABLED="true"
ENVB64="$(printf '%s' "ENVIRONMENT=$ENVIRONMENT
DB_HOST=$DB_HOST
DB_PORT=$DB_PORT
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASS
DB_SCHEMA=$DB_SCHEMA
REDIS_HOST=${REDIS_HOST:-}
REDIS_PORT=${REDIS_PORT:-6580}
REDIS_PASSWORD=${REDIS_PASS:-}
CACHE_ENABLED=$CACHE_ENABLED
ADS_ROOT_PATH=$ADS_ROOT_PATH" | base64 | tr -d '\n')"
DBPW_B64="$(printf %s "$DB_PASS" | base64 | tr -d '\n')"

echo ">> Building, bootstrapping leo_ads schema, and (re)starting the container ..."
ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$ADS_PORT" "$ENVB64" "$DB_HOST" "$DB_PORT" "$DB_NAME" "$DB_USER" "$DBPW_B64" "$DB_SCHEMA" "$SEED_SAMPLE" "$DEPLOY_MODE" "$IMAGE" "$GHCR_USER" "$(printf %s "$GHCR_TOKEN" | base64 | tr -d '\n')" <<'REMOTE'
set -euo pipefail
PORT="$1"; ENVB64="$2"; DBHOST="$3"; DBPORT="$4"; DBNAME="$5"; DBUSER="$6"; DBPW="$(printf %s "$7" | base64 -d)"; SCHEMA="$8"; SEED="$9"
DEPLOY_MODE="${10:-build}"; IMAGE="${11:-}"; GHCR_USER="${12:-token}"; GHCR_TOKEN="$(printf %s "${13:-}" | base64 -d 2>/dev/null || true)"
command -v docker >/dev/null 2>&1 || { sudo apt-get update -qq; sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io; sudo systemctl enable --now docker; }
command -v psql   >/dev/null 2>&1 || { sudo apt-get update -qq; sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-client; }

echo "   bootstrapping leo_ads schema (idempotent) ..."
export PGPASSWORD="$DBPW"
psql -v ON_ERROR_STOP=1 -h "$DBHOST" -p "$DBPORT" -U "$DBUSER" -d "$DBNAME" -f /opt/c360/ads-server/sql-scripts/db-schema-init.sql >/dev/null
if [ "$SEED" = "true" ]; then
  echo "   loading sample data ..."
  psql -v ON_ERROR_STOP=1 -h "$DBHOST" -p "$DBPORT" -U "$DBUSER" -d "$DBNAME" -f /opt/c360/ads-server/sql-scripts/sample-data-init.sql >/dev/null
fi

umask 077; env_file="$(mktemp)"; printf '%s' "$ENVB64" | base64 -d > "$env_file"
sudo mkdir -p /opt/c360; sudo mv "$env_file" /opt/c360/ads.env; sudo chmod 600 /opt/c360/ads.env
if [ "$DEPLOY_MODE" = "ghcr" ]; then
  echo "   pulling $IMAGE ..."
  [ -n "$GHCR_TOKEN" ] && printf %s "$GHCR_TOKEN" | sudo docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
  sudo docker pull "$IMAGE"
  RUN_IMG="$IMAGE"
else
  sed -i 's/ --mount=[^ ]*//g' /opt/c360/ads-server/Dockerfile   # docker.io: no buildx
  sudo docker build -t customer360-ads /opt/c360/ads-server
  RUN_IMG="customer360-ads"
fi
sudo docker rm -f customer360-ads >/dev/null 2>&1 || true
sudo docker run -d --name customer360-ads --restart unless-stopped --network host --env-file /opt/c360/ads.env "$RUN_IMG"
sleep 3
curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && echo "   health OK (:$PORT/health)" || echo "   WARN: health not ready yet"
sudo docker ps --filter name=customer360-ads --format '   running: {{.Names}} ({{.Status}})'
REMOTE
echo ">> Done. Expose via the LB (add an 'ads' backend -> <box-ip>:$ADS_PORT) if it needs public access."
