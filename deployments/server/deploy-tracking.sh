#!/usr/bin/env bash
# Deploy data-tracking-api (FastAPI event ingestion) onto its OWN "tracking" server VM,
# together with a DEDICATED broker Redis co-located on that box.
#   ./deploy-tracking.sh <uat|prod>
#
# Ships the repo's data-tracking-api/ to the box over SSH (or pulls the GHCR image), installs
# Docker if missing, runs a broker Redis (AOF + noeviction) on the box, and (re)runs the app as
# a container with --network host on :8010, wired to:
#   * S3-compatible object storage  (../storage — vStorage on VNG; per-source NDJSON buckets)
#   * broker Redis (127.0.0.1)      — session cache + IP rate limit + the cdp:events:raw stream
#   * Jaeger via OTLP               (../monitoring — request tracing, off on uat unless enabled)
#
# WHY a dedicated broker Redis on this box (not the api-box cache Redis): keeps the hot-path XADD
# on loopback (sub-ms) and self-contained (ingestion survives the api/backend box being down), and
# isolates the high-throughput event stream from the latency-sensitive API/auth cache. See
# deployments/docs/web-tracking-implementation-plan.md §6. The Loader (backend box) consumes this
# broker over the private VPC — open 6580 on the tracking box from the backend box (see
# ../server/overlays/<env>.tfvars extra_ingress) and set BROKER_REDIS_* on the backend deploy.
#
# UNLIKE deploy-api.sh this runs on a DEDICATED box (server key "tracking"), so Jaeger is reached
# over the private VPC at the monitoring box's fixed IP (NOT 127.0.0.1) — port 4318 must be open on
# the secgroup from this box; and Caddy reaches :8010 from the api box (8010 open from 10.100.1.5).
#
# Re-runnable. Target box = servers["$TRACKING_SERVER_KEY"] (default "tracking"). Overrides:
#   BASTION_USER / SSH_KEY / TRACKING_SERVER_KEY / MON_SERVER_KEY
#   BUILD_LOCAL=1 to build on the VM (default 0 = pull the CI-built GHCR image; use 1 for the very
#     first deploy before ci.yml has published data-tracking-api on main)
#   OTEL_ENABLED / OTEL_ENDPOINT / OTEL_SAMPLER_ARG  (see ../lib/otel.sh)
#   S3_AUTO_CREATE_BUCKETS (default true — see the vStorage per-source-bucket caveat in ../storage/README.md)
#   BROKER_REDIS_PASSWORD (else read from ./.env; auto-generated + appended to ./.env on first run)
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
MON_SERVER_KEY="${MON_SERVER_KEY:-api}"       # Jaeger runs on the monitoring box (uat = api box)

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
TRK_PRIV="$(srv_ip "$TRACKING_SERVER_KEY" fixed_ip)"
[[ -n "$TRK_FIP" ]] || { echo "ERROR: no floating IP for server key '$TRACKING_SERVER_KEY' — add it to overlays/$ENV.tfvars (servers) with attach_floating and apply."; exit 1; }
BASTION="${BASTION_USER:-leocdp360}@$TRK_FIP"
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
echo ">> Target (tracking): $BASTION   private=${TRK_PRIV:-<unknown>}"

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

# --- Dedicated broker Redis (co-located on the tracking box). Password persists in ./.env so
#     the Loader deploy (backend box) can reuse it; auto-generate on first run. ---
BROKER_REDIS_PORT="${BROKER_REDIS_PORT:-6580}"
BROKER_REDIS_IMAGE="${BROKER_REDIS_IMAGE:-redis:7-alpine}"
BROKER_REDIS_MEM="${BROKER_REDIS_MEM:-512m}"   # docker --memory cap (protect the 1 vCPU / 2 GB box)
if [[ -z "${BROKER_REDIS_PASSWORD:-}" ]]; then
  BROKER_REDIS_PASSWORD="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32 || true)"
  printf '\n# Auto-generated by deploy-tracking.sh — broker Redis on the tracking box (Loader reuses it).\nBROKER_REDIS_PASSWORD=%s\n' "$BROKER_REDIS_PASSWORD" >> .env
  echo ">> Broker Redis: generated a new password and saved it to ./.env (copy it to your password manager)."
fi
STREAM_KEY="${TRACKING_STREAM_KEY:-cdp:events:raw}"
STREAM_MAXLEN="${TRACKING_STREAM_MAXLEN:-1000000}"
echo ">> Broker Redis: 127.0.0.1:$BROKER_REDIS_PORT (AOF, noeviction)  stream=$STREAM_KEY (MAXLEN ~ $STREAM_MAXLEN)"

# --- Redis data viewer (redis-commander): a lightweight web UI to browse/edit the broker's
#     keys + the cdp:events:raw stream. Pre-wired to the broker and protected by its OWN HTTP
#     basic-auth login, so it is exposed DIRECTLY on the LB (member_port = REDIS_VIEWER_PORT in
#     ../load_balancer/overlays/<env>.tfvars) — same "own-login, no oauth2 gate" model as
#     Portainer/pgAdmin. Hence it binds REDIS_VIEWER_BIND (0.0.0.0 so the LB can reach it), NOT
#     loopback. Password persists in ./.env (auto-gen). CLEARTEXT CAVEAT: the uat L4 LB has no
#     TLS and redis-commander serves plain HTTP, so the basic-auth login crosses the wire in
#     cleartext (same accepted uat tradeoff as pgAdmin) — harden later with Caddy TLS or an SSO
#     gate. For tunnel-only instead, set REDIS_VIEWER_BIND=127.0.0.1 + drop the LB backend.
#     Turn off with REDIS_VIEWER_ENABLED=false to reclaim RAM on the 1 vCPU / 2 GB box. ---
REDIS_VIEWER_ENABLED="${REDIS_VIEWER_ENABLED:-true}"
REDIS_VIEWER_IMAGE="${REDIS_VIEWER_IMAGE:-rediscommander/redis-commander:latest}"
REDIS_VIEWER_PORT="${REDIS_VIEWER_PORT:-8081}"
REDIS_VIEWER_BIND="${REDIS_VIEWER_BIND:-0.0.0.0}"   # 0.0.0.0 = reachable by the LB; 127.0.0.1 = tunnel-only
REDIS_VIEWER_MEM="${REDIS_VIEWER_MEM:-128m}"
REDIS_VIEWER_USER="${REDIS_VIEWER_USER:-admin}"
# Served behind Caddy TLS at https://<domain>$REDIS_VIEWER_URL_PREFIX. redis-commander is a SPA
# with absolute asset paths, so it must emit prefixed URLs (URL_PREFIX) and Caddy must forward the
# prefix UNSTRIPPED — like Jaeger's QUERY_BASE_PATH. Set empty for raw root (tunnel/LB-direct).
REDIS_VIEWER_URL_PREFIX="${REDIS_VIEWER_URL_PREFIX:-/redis}"
if [[ "$REDIS_VIEWER_ENABLED" == "true" && -z "${REDIS_VIEWER_PASSWORD:-}" ]]; then
  REDIS_VIEWER_PASSWORD="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24 || true)"
  printf '\n# Auto-generated by deploy-tracking.sh — redis-commander (broker viewer) HTTP basic-auth password.\nREDIS_VIEWER_PASSWORD=%s\n' "$REDIS_VIEWER_PASSWORD" >> .env
  echo ">> Redis viewer: generated a basic-auth password and saved it to ./.env (login user '$REDIS_VIEWER_USER')."
fi
[[ "$REDIS_VIEWER_ENABLED" == "true" ]] && echo ">> Redis viewer: redis-commander on $REDIS_VIEWER_BIND:$REDIS_VIEWER_PORT (own login; LB backend), pre-wired to the broker."

# --- CD image source: pull the CI-built image from GHCR, or build on the VM. Matches the other
#     services (default = GHCR). ci.yml publishes data-tracking-api on main; for the very first
#     deploy before that, run with BUILD_LOCAL=1. ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/ghcr.sh"
SERVICE="data-tracking-api"          # source dir + GHCR image name (matches the CI matrix key)
CONTAINER="customer360-tracking-api" # runtime container name (matches dev-docker-compose.yml)
GHCR_USER="${GHCR_USER:-${GITHUB_ACTOR:-token}}"
GHCR_TOKEN="${GHCR_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ "${BUILD_LOCAL:-0}" == "1" ]]; then
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

# --- OpenTelemetry request tracing (OTLP -> Jaeger). OFF on uat, 10% on prod; override with
#     OTEL_ENABLED / OTEL_ENDPOINT / OTEL_SAMPLER_ARG. Jaeger runs on the monitoring box, so the
#     endpoint is that box's FIXED ip:4318 (this service is never co-located with Jaeger). ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/otel.sh"
JAEGER_HOST="$(srv_ip "$MON_SERVER_KEY" fixed_ip)"; JAEGER_HOST="${JAEGER_HOST:-127.0.0.1}"
OTEL_ENABLED="${OTEL_ENABLED:-$(tfval otel_enabled "overlays/$ENV.tfvars")}"
OTEL_B64="$(otel_env_lines "$SERVICE" "$ENV" "$JAEGER_HOST" | base64 | tr -d '\n')"

# --- build + run on the VM. ALL params travel as ONE base64 blob of KEY=VALUE lines, decoded +
#     sourced on the box. This dodges ssh arg-flattening: `ssh 'bash -s' a b c` joins the args into
#     one space-separated string the remote shell re-splits, so an EMPTY arg (e.g. IMAGE on a local
#     build, or an absent GHCR token) collapses and shifts every later positional arg — which would
#     silently corrupt OTEL_B64 / the container name. Secrets stay individually base64'd inside. ---
echo ">> Installing Docker (if needed), starting broker Redis, building, and (re)starting the app ..."
S3_SECRET_B64="$(printf %s "$S3_SECRET_KEY" | base64 | tr -d '\n')"
BROKER_PW_B64="$(printf %s "$BROKER_REDIS_PASSWORD" | base64 | tr -d '\n')"
GHCR_TOKEN_B64="$(printf %s "$GHCR_TOKEN" | base64 | tr -d '\n')"
VIEWER_PW_B64="$(printf %s "${REDIS_VIEWER_PASSWORD:-}" | base64 | tr -d '\n')"
PARAMS_B64="$(printf '%s\n' \
  "S3_ENDPOINT=$S3_ENDPOINT" "S3_REGION=$S3_REGION" "S3_ACCESS_KEY=$S3_ACCESS_KEY" "S3_SECRET_B64=$S3_SECRET_B64" "S3_AUTO_CREATE=$S3_AUTO_CREATE" \
  "BROKER_PORT=$BROKER_REDIS_PORT" "BROKER_PW_B64=$BROKER_PW_B64" "BROKER_IMG=$BROKER_REDIS_IMAGE" "BROKER_MEM=$BROKER_REDIS_MEM" "STREAM_KEY=$STREAM_KEY" "STREAM_MAXLEN=$STREAM_MAXLEN" \
  "DEPLOY_MODE=$DEPLOY_MODE" "IMAGE=$IMAGE" "GHCR_USER=$GHCR_USER" "GHCR_TOKEN_B64=$GHCR_TOKEN_B64" "OTEL_B64=$OTEL_B64" "CONTAINER=$CONTAINER" \
  "RV_EN=$REDIS_VIEWER_ENABLED" "RV_IMG=$REDIS_VIEWER_IMAGE" "RV_PORT=$REDIS_VIEWER_PORT" "RV_MEM=$REDIS_VIEWER_MEM" "RV_USER=$REDIS_VIEWER_USER" "RV_PW_B64=$VIEWER_PW_B64" "RV_BIND=$REDIS_VIEWER_BIND" "RV_PREFIX=$REDIS_VIEWER_URL_PREFIX" \
  | base64 | tr -d '\n')"
ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$PARAMS_B64" <<'REMOTE'
set -euo pipefail
tmp="$(mktemp)"; printf %s "$1" | base64 -d > "$tmp"; set -a; . "$tmp"; set +a; rm -f "$tmp"
# Decode the individually-b64'd secrets carried inside the blob.
S3_SECRET_KEY="$(printf %s "$S3_SECRET_B64" | base64 -d)"
BROKER_PW="$(printf %s "$BROKER_PW_B64" | base64 -d)"
GHCR_TOKEN="$(printf %s "${GHCR_TOKEN_B64:-}" | base64 -d 2>/dev/null || true)"
RV_PW="$(printf %s "${RV_PW_B64:-}" | base64 -d 2>/dev/null || true)"
DEPLOY_MODE="${DEPLOY_MODE:-build}"; IMAGE="${IMAGE:-}"; GHCR_USER="${GHCR_USER:-token}"; CONTAINER="${CONTAINER:-customer360-tracking-api}"
BROKER_PORT="${BROKER_PORT:-6580}"; BROKER_IMG="${BROKER_IMG:-redis:7-alpine}"; BROKER_MEM="${BROKER_MEM:-512m}"
RV_EN="${RV_EN:-false}"; RV_IMG="${RV_IMG:-rediscommander/redis-commander:latest}"; RV_PORT="${RV_PORT:-8081}"; RV_MEM="${RV_MEM:-128m}"; RV_USER="${RV_USER:-admin}"; RV_BIND="${RV_BIND:-0.0.0.0}"; RV_PREFIX="${RV_PREFIX:-}"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  sudo systemctl enable --now docker
fi

# --- broker Redis: AOF (everysec) so a restart doesn't lose buffered-but-unflushed events, and
#     noeviction so a flood never silently drops un-consumed stream entries. Loopback + the private
#     VPC only (the app is --network host on this box; the Loader reaches it on the private ip). ---
sudo docker volume create c360_broker_redis_data >/dev/null
sudo docker rm -f c360-broker-redis >/dev/null 2>&1 || true
sudo docker run -d --name c360-broker-redis --restart unless-stopped --network host \
  --memory "$BROKER_MEM" -v c360_broker_redis_data:/data "$BROKER_IMG" \
  redis-server --port "$BROKER_PORT" --requirepass "$BROKER_PW" \
  --appendonly yes --appendfsync everysec --maxmemory-policy noeviction
sleep 2
sudo docker ps --filter name=c360-broker-redis --format '   broker redis: {{.Names}} ({{.Status}})'

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
REDIS_HOST=127.0.0.1
REDIS_PORT=$BROKER_PORT
REDIS_PASSWORD=$BROKER_PW
REDIS_DB=0
TRACKING_STREAM_ENABLED=true
TRACKING_STREAM_KEY=$STREAM_KEY
TRACKING_STREAM_MAXLEN=$STREAM_MAXLEN
ENVF
sudo mkdir -p /opt/c360
if [ -n "$OTEL_B64" ]; then printf '%s' "$OTEL_B64" | base64 -d >> "$env_file"; fi
sudo mv "$env_file" /opt/c360/tracking.env
sudo chmod 600 /opt/c360/tracking.env
if [ "$DEPLOY_MODE" = "ghcr" ]; then
  echo "   pulling $IMAGE ..."
  [ -n "$GHCR_TOKEN" ] && printf %s "$GHCR_TOKEN" | sudo docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
  sudo docker pull "$IMAGE"
  RUN_IMG="$IMAGE"
else
  echo "   building image (a few minutes on a small box)..."
  # docker.io ships no buildx, so strip the BuildKit-only `RUN --mount` (a pip-cache opt) and
  # use the classic builder — same trick as deploy-api.sh.
  sed -i 's/ --mount=[^ ]*//g' /opt/c360/data-tracking-api/Dockerfile
  sudo docker build -t data-tracking-api /opt/c360/data-tracking-api
  RUN_IMG="data-tracking-api"
fi
sudo docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
sudo docker run -d --name "$CONTAINER" --restart unless-stopped --network host --env-file /opt/c360/tracking.env "$RUN_IMG"
sleep 3
sudo docker ps --filter name="$CONTAINER" --format '   running: {{.Names}} ({{.Status}}) image={{.Image}}'

# --- Redis data viewer (redis-commander): own HTTP basic-auth login, exposed on the LB ---
# Runs on the DEFAULT bridge (not --network host) so `-p $RV_BIND:PORT:8081` controls exactly
# which interface the UI binds (0.0.0.0 = LB-reachable; 127.0.0.1 = tunnel-only); it reaches the
# host-net broker via host.docker.internal:host-gateway. Its own basic-auth login is the
# protection (no oauth2 gate, like Portainer/pgAdmin). The broker password is alnum, so it is
# safe inside the colon-delimited REDIS_HOSTS value.
if [ "$RV_EN" = "true" ]; then
  echo "   deploying redis-commander (broker viewer) bound $RV_BIND:$RV_PORT ..."
  sudo docker pull "$RV_IMG" >/dev/null 2>&1 || true
  sudo docker rm -f c360-redis-viewer >/dev/null 2>&1 || true
  sudo docker run -d --name c360-redis-viewer --restart unless-stopped \
    --memory "$RV_MEM" --add-host host.docker.internal:host-gateway \
    -e "REDIS_HOSTS=broker:host.docker.internal:$BROKER_PORT:0:$BROKER_PW" \
    -e HTTP_USER="$RV_USER" -e HTTP_PASSWORD="$RV_PW" -e URL_PREFIX="$RV_PREFIX" \
    -p "$RV_BIND:$RV_PORT:8081" "$RV_IMG"
  sleep 2
  sudo docker ps --filter name=c360-redis-viewer --format '   redis viewer: {{.Names}} ({{.Status}})'
else
  sudo docker rm -f c360-redis-viewer >/dev/null 2>&1 || true  # honor REDIS_VIEWER_ENABLED=false
fi
REMOTE

echo ">> Done. data-tracking-api is on the VM at :8010 (health: /health); broker Redis on :$BROKER_REDIS_PORT."
echo "   Public (after Caddy /data route + LB): https://beta.leocdp.com/data/api/v1/tracking/logs"
echo "   Direct (admin tunnel): ssh -i $SSH_KEY -L 8010:localhost:8010 $BASTION  # http://localhost:8010/health"
if [[ "$REDIS_VIEWER_ENABLED" == "true" ]]; then
  if [[ -n "$REDIS_VIEWER_URL_PREFIX" ]]; then
    echo "   Redis viewer (redis-commander): https://<caddy-domain>$REDIS_VIEWER_URL_PREFIX  (own login '$REDIS_VIEWER_USER' / REDIS_VIEWER_PASSWORD from ./.env; broker pre-added)"
    echo "     -> add the '$REDIS_VIEWER_URL_PREFIX' route in ../proxy/Caddyfile (redis_upstream=$TRK_PRIV:$REDIS_VIEWER_PORT), then: (cd ../proxy && ./deploy-caddy.sh $ENV)"
  else
    echo "   Redis viewer (redis-commander): http://<lb-ip>:$REDIS_VIEWER_PORT  (own login '$REDIS_VIEWER_USER' / REDIS_VIEWER_PASSWORD from ./.env; broker pre-added)"
  fi
  echo "     admin fallback: ssh -i $SSH_KEY -L $REDIS_VIEWER_PORT:localhost:$REDIS_VIEWER_PORT $BASTION  # http://localhost:$REDIS_VIEWER_PORT$REDIS_VIEWER_URL_PREFIX"
fi
echo "   Loader: point the backend-system event_loader at BROKER_REDIS_HOST=${TRK_PRIV:-<tracking-private-ip>} :$BROKER_REDIS_PORT (see backend-system/event_loader)."

# --- release ledger: record this deploy to the GitHub Deployments API (best-effort) ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/record_deploy.sh"
record_deployment "$ENV" "$SERVICE" "${IMAGE:-local}" success
