#!/usr/bin/env bash
# Deploy data-tracking-api (FastAPI event ingestion) onto its OWN "tracking" server VM.
#   ./deploy-tracking.sh <uat|prod>
#
# Minimal by design — deploys ONLY what the app actually uses:
#   * S3-compatible object storage (../storage — vStorage on VNG): the durable NDJSON sink.
#   * Redis (../cache, on the api box): OPTIONAL — IP rate-limit + session cache. The app
#     fails OPEN if Redis is absent, so this is best-effort. Reuses the existing api-box Redis
#     (no dedicated instance); reached over the private VPC (open 6580 api<-tracking in
#     ../server/overlays/<env>.tfvars extra_ingress).
# The app is exposed publicly at https://<caddy_domain>/data via Caddy (../proxy) + the LB.
#
# Re-runnable. Target box = servers["$TRACKING_SERVER_KEY"] (default "tracking"). Overrides:
#   BASTION_USER / SSH_KEY / TRACKING_SERVER_KEY / REDIS_SERVER_KEY
#   BUILD_LOCAL (default 1 — data-tracking-api is not built by CI, so it builds on the VM)
#   S3_AUTO_CREATE_BUCKETS (default true — per-source buckets; see ../storage/README.md caveat)
set -euo pipefail
cd "$(dirname "$0")"                 # deployments/server
REPO_ROOT="$(cd ../.. && pwd)"       # repo root (contains data-tracking-api/)

ENV="${1:-}"
case "$ENV" in
  uat | prod) ;;
  *) echo "Usage: ./deploy-tracking.sh <uat|prod>"; exit 1 ;;
esac

[[ -f .env ]] && { set -a; source ./.env; set +a; }
SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"
TRACKING_SERVER_KEY="${TRACKING_SERVER_KEY:-tracking}"
REDIS_SERVER_KEY="${REDIS_SERVER_KEY:-api}"   # the box that runs the shared cache Redis (uat)
MON_SERVER_KEY="${MON_SERVER_KEY:-api}"       # the box that runs Jaeger (uat = api box)

# Read a tfvars value: content between quotes for strings (keeps '#'), or the bare token with
# any trailing comment stripped for unquoted numbers/bools (same helper as deploy-api.sh).
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
srv_ip() { printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get(sys.argv[2]) for i in (s.get("internal_interfaces") or []) if i.get(sys.argv[2])), ""))' "$1" "$2"; }
TRK_FIP="$(srv_ip "$TRACKING_SERVER_KEY" floating_ip)"
[[ -n "$TRK_FIP" ]] || { echo "ERROR: no floating IP for server key '$TRACKING_SERVER_KEY' — add it to overlays/$ENV.tfvars (servers) with attach_floating and apply."; exit 1; }
BASTION="${BASTION_USER:-leocdp360}@$TRK_FIP"
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
echo ">> Target (tracking): $BASTION"

# --- Object storage (S3-compatible) from the ../storage deployment ---
store="../storage"
S3_ENDPOINT="$(tfval s3_endpoint "$store/overlays/$ENV.tfvars")"
S3_REGION="$(tfval region "$store/overlays/$ENV.tfvars")"; S3_REGION="${S3_REGION:-us-east-1}"
S3_ACCESS_KEY="${TF_VAR_access_key:-$(tfval access_key "$store/terraform.tfvars")}"
S3_SECRET_KEY="${TF_VAR_secret_key:-$(tfval secret_key "$store/terraform.tfvars")}"
S3_AUTO_CREATE="${S3_AUTO_CREATE_BUCKETS:-true}"
: "${S3_ENDPOINT:?could not read s3_endpoint from $store/overlays/$ENV.tfvars}"
: "${S3_ACCESS_KEY:?missing vStorage access_key (set TF_VAR_access_key or $store/terraform.tfvars)}"
: "${S3_SECRET_KEY:?missing vStorage secret_key (set TF_VAR_secret_key or $store/terraform.tfvars)}"
echo ">> S3: $S3_ENDPOINT (region $S3_REGION, path-style, auto_create=$S3_AUTO_CREATE)"

# --- Redis (optional) — reuse the api-box cache Redis over the private VPC. Absent -> caching
#     stays off and the rate limiter fails open (the app still ingests to S3). ---
cache="../cache"
REDIS_PASS="${TF_VAR_redis_password:-$(tfval redis_password "$cache/terraform.tfvars")}"
REDIS_HOST=""; REDIS_PORT=""
if [[ -n "$REDIS_PASS" ]]; then
  REDIS_HOST="$(srv_ip "$REDIS_SERVER_KEY" fixed_ip)"   # api box private IP (Redis --network host)
  REDIS_PORT="$(tfval redis_port "$cache/overlays/$ENV.tfvars")"; REDIS_PORT="${REDIS_PORT:-6580}"
fi
if [[ -n "$REDIS_HOST" ]]; then echo ">> Redis: ${REDIS_HOST}:${REDIS_PORT} (rate-limit + session cache)"; else echo ">> Redis: not configured — caching off, rate limiter fails open."; fi

# --- CD image source: build on the VM by default (data-tracking-api is not published by CI);
#     set BUILD_LOCAL=0 to pull a GHCR image once one exists. ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/ghcr.sh"
SERVICE="data-tracking-api"          # source dir + (future) GHCR image name
CONTAINER="customer360-tracking-api" # runtime container name (matches dev-docker-compose.yml)
GHCR_USER="${GHCR_USER:-${GITHUB_ACTOR:-token}}"
GHCR_TOKEN="${GHCR_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ "${BUILD_LOCAL:-1}" == "1" ]]; then
  DEPLOY_MODE="build"; IMAGE=""
  echo ">> Image: BUILD_LOCAL=1 — building $SERVICE on the VM from source."
  echo ">> Shipping data-tracking-api/ ..."
  tar -C "$REPO_ROOT" -czf - data-tracking-api \
    | ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo mkdir -p /opt/c360 && sudo chown "$(id -un)" /opt/c360 && tar -C /opt/c360 -xzf -'
else
  DEPLOY_MODE="ghcr"
  IMAGE="$(image_ref "$SERVICE" "$(resolve_tag "overlays/$ENV.tfvars")")"
  echo ">> Image: $IMAGE   (pull from GHCR; BUILD_LOCAL=1 to build on the VM)"
fi

# --- OpenTelemetry request tracing (OTLP -> the api-box Jaeger). OFF unless otel_enabled
#     (overlays/<env>.tfvars) or OTEL_ENABLED=true. Jaeger runs on the monitoring box; reached at
#     its private ip:4318 over the VPC (open 4318 api<-tracking in server extra_ingress). ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/otel.sh"
JAEGER_HOST="$(srv_ip "$MON_SERVER_KEY" fixed_ip)"; JAEGER_HOST="${JAEGER_HOST:-127.0.0.1}"
OTEL_ENABLED="${OTEL_ENABLED:-$(tfval otel_enabled "overlays/$ENV.tfvars")}"
OTEL_B64="$(otel_env_lines "$SERVICE" "$ENV" "$JAEGER_HOST" | base64 | tr -d '\n')"

# --- build + run on the VM. ALL params travel as ONE base64 blob of KEY=VALUE lines, decoded +
#     sourced on the box — this dodges ssh arg-flattening (empty args like IMAGE on a local build
#     collapse and shift later positional args). Secrets are individually base64'd inside. ---
echo ">> Installing Docker (if needed), building, and (re)starting the app ..."
S3_SECRET_B64="$(printf %s "$S3_SECRET_KEY" | base64 | tr -d '\n')"
REDIS_PW_B64="$(printf %s "${REDIS_PASS:-}" | base64 | tr -d '\n')"
GHCR_TOKEN_B64="$(printf %s "$GHCR_TOKEN" | base64 | tr -d '\n')"
PARAMS_B64="$(printf '%s\n' \
  "S3_ENDPOINT=$S3_ENDPOINT" "S3_REGION=$S3_REGION" "S3_ACCESS_KEY=$S3_ACCESS_KEY" "S3_SECRET_B64=$S3_SECRET_B64" "S3_AUTO_CREATE=$S3_AUTO_CREATE" \
  "REDIS_HOST=${REDIS_HOST:-}" "REDIS_PORT=${REDIS_PORT:-}" "REDIS_PW_B64=$REDIS_PW_B64" \
  "DEPLOY_MODE=$DEPLOY_MODE" "IMAGE=$IMAGE" "GHCR_USER=$GHCR_USER" "GHCR_TOKEN_B64=$GHCR_TOKEN_B64" "CONTAINER=$CONTAINER" "OTEL_B64=$OTEL_B64" \
  | base64 | tr -d '\n')"
ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$PARAMS_B64" <<'REMOTE'
set -euo pipefail
tmp="$(mktemp)"; printf %s "$1" | base64 -d > "$tmp"; set -a; . "$tmp"; set +a; rm -f "$tmp"
S3_SECRET_KEY="$(printf %s "$S3_SECRET_B64" | base64 -d)"
REDIS_PW="$(printf %s "${REDIS_PW_B64:-}" | base64 -d 2>/dev/null || true)"
GHCR_TOKEN="$(printf %s "${GHCR_TOKEN_B64:-}" | base64 -d 2>/dev/null || true)"
DEPLOY_MODE="${DEPLOY_MODE:-build}"; IMAGE="${IMAGE:-}"; GHCR_USER="${GHCR_USER:-token}"; CONTAINER="${CONTAINER:-customer360-tracking-api}"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  sudo systemctl enable --now docker
fi
umask 077
env_file="$(mktemp)"
cat > "$env_file" <<ENVF
ENVIRONMENT=production
OBJECT_STORAGE_MODE=s3
S3_ENDPOINT_URL=$S3_ENDPOINT
S3_REGION=$S3_REGION
S3_ACCESS_KEY_ID=$S3_ACCESS_KEY
S3_SECRET_ACCESS_KEY=$S3_SECRET_KEY
S3_FORCE_PATH_STYLE=true
S3_AUTO_CREATE_BUCKETS=$S3_AUTO_CREATE
ENVF
if [ -n "${REDIS_HOST:-}" ] && [ -n "$REDIS_PW" ]; then
  cat >> "$env_file" <<ENVR
REDIS_HOST=$REDIS_HOST
REDIS_PORT=${REDIS_PORT:-6580}
REDIS_PASSWORD=$REDIS_PW
REDIS_DB=0
ENVR
fi
sudo mkdir -p /opt/c360
if [ -n "${OTEL_B64:-}" ]; then printf '%s' "$OTEL_B64" | base64 -d >> "$env_file"; fi
sudo mv "$env_file" /opt/c360/tracking.env
sudo chmod 600 /opt/c360/tracking.env
if [ "$DEPLOY_MODE" = "ghcr" ]; then
  echo "   pulling $IMAGE ..."
  [ -n "$GHCR_TOKEN" ] && printf %s "$GHCR_TOKEN" | sudo docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
  sudo docker pull "$IMAGE"
  RUN_IMG="$IMAGE"
else
  echo "   building image (a few minutes on a small box)..."
  # docker.io ships no buildx, so strip the BuildKit-only `RUN --mount` (a pip-cache opt).
  sed -i 's/ --mount=[^ ]*//g' /opt/c360/data-tracking-api/Dockerfile
  sudo docker build -t data-tracking-api /opt/c360/data-tracking-api
  RUN_IMG="data-tracking-api"
fi
sudo docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
sudo docker run -d --name "$CONTAINER" --restart unless-stopped --network host --env-file /opt/c360/tracking.env "$RUN_IMG"
sleep 3
sudo docker ps --filter name="$CONTAINER" --format '   running: {{.Names}} ({{.Status}}) image={{.Image}}'
REMOTE

echo ">> Done. data-tracking-api is on the VM at :8010 (health: /health)."
echo "   Public (Caddy /data + LB): https://beta.leocdp.com/data/api/v1/tracking/logs"
echo "   Direct (admin tunnel): ssh -i $SSH_KEY -L 8010:localhost:8010 $BASTION  # http://localhost:8010/health"

# --- release ledger: record this deploy to the GitHub Deployments API (best-effort) ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/record_deploy.sh"
record_deployment "$ENV" "$SERVICE" "${IMAGE:-local}" success
