#!/usr/bin/env bash
# Deploy data-tracking-api (FastAPI event ingestion) onto its OWN "tracking" server VM,
# as N auto-load-balanced replicas behind a local nginx round-robin LB.
#   ./deploy-tracking.sh <uat|prod>
#
# Minimal by design — deploys ONLY what the app actually uses:
#   * S3-compatible object storage (../storage — vStorage on VNG): the durable NDJSON sink.
#   * Redis (../cache, on the api box): OPTIONAL — IP rate-limit + session cache. The app
#     fails OPEN if Redis is absent, so this is best-effort. Reuses the existing api-box Redis
#     (no dedicated instance); reached over the private VPC (open 6580 api<-tracking in
#     ../server/overlays/<env>.tfvars extra_ingress).
#   * nginx (on this box): a tiny local load balancer that owns host :8010 (what Caddy's
#     DATA_UPSTREAM targets) and least_conn round-robins across the N app replicas, which
#     live on a private docker bridge — so scaling is fully contained here (Caddy, the NLB,
#     and the proxy overlays' data_upstream stay UNCHANGED, still just ip:8010).
# The app is exposed publicly at https://<caddy_domain>/data via Caddy (../proxy) + the LB.
#
# Re-runnable. Target box = servers["$TRACKING_SERVER_KEY"] (default "tracking"). Overrides:
#   BASTION_USER / SSH_KEY / TRACKING_SERVER_KEY / REDIS_SERVER_KEY
#   TRACKING_REPLICAS (how many app instances behind the local LB; default uat=3, prod=5)
#   TRACKING_LB_IMAGE (nginx image for the LB; default nginx:alpine)
#   TRACKING_NETWORK  (private docker bridge name; default c360-tracking)
#   BUILD_LOCAL (default 0 — data-tracking-api is now built + published to GHCR by CI, so it
#               pulls the image; set BUILD_LOCAL=1 to build on the VM from source instead)
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

# --- how many app replicas run behind the local nginx LB on THIS box (auto load-balance).
#     Default per env; override with TRACKING_REPLICAS. The nginx LB owns host :8010 (what
#     Caddy's DATA_UPSTREAM targets) and least_conn round-robins across the replicas. ---
case "$ENV" in uat) DEFAULT_REPLICAS=3 ;; prod) DEFAULT_REPLICAS=5 ;; esac
REPLICAS="${TRACKING_REPLICAS:-$DEFAULT_REPLICAS}"
[[ "$REPLICAS" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: TRACKING_REPLICAS must be a positive integer (got '$REPLICAS')."; exit 1; }
LB_IMAGE="${TRACKING_LB_IMAGE:-nginx:alpine}"
NETWORK="${TRACKING_NETWORK:-c360-tracking}"

# --- optional rate-limit override (app default 120 req / 60s per client IP as seen by the app).
#     Convenience: TRACKING_RATE_LIMIT_RPS=<n> sets requests=<n>, window=1s (a hard n/second cap).
#     Or set TRACKING_RATE_LIMIT_REQUESTS / TRACKING_RATE_LIMIT_WINDOW_SECONDS directly. Unset ->
#     the app default. Written into /opt/c360/tracking.env; picked up on replica (re)start. ---
RL_REQUESTS="${TRACKING_RATE_LIMIT_REQUESTS:-}"
RL_WINDOW="${TRACKING_RATE_LIMIT_WINDOW_SECONDS:-}"
if [[ -n "${TRACKING_RATE_LIMIT_RPS:-}" ]]; then RL_REQUESTS="$TRACKING_RATE_LIMIT_RPS"; RL_WINDOW="1"; fi
[[ -n "$RL_REQUESTS" ]] && echo ">> Rate limit: $RL_REQUESTS req / ${RL_WINDOW:-60}s per client IP" || echo ">> Rate limit: app default (120 req / 60s)"

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

# --- CD image source: pull the CI-built image from GHCR by default (data-tracking-api is now
#     published by CI, like the other services); set BUILD_LOCAL=1 to build on the VM instead. ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/ghcr.sh"
SERVICE="data-tracking-api"          # source dir + GHCR image name (ghcr.io/leo-cdp/leo-customer360/data-tracking-api)
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
  "REPLICAS=$REPLICAS" "LB_IMAGE=$LB_IMAGE" "NETWORK=$NETWORK" "RL_REQUESTS=$RL_REQUESTS" "RL_WINDOW=$RL_WINDOW" \
  | base64 | tr -d '\n')"
ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$PARAMS_B64" <<'REMOTE'
set -euo pipefail
tmp="$(mktemp)"; printf %s "$1" | base64 -d > "$tmp"; set -a; . "$tmp"; set +a; rm -f "$tmp"
S3_SECRET_KEY="$(printf %s "$S3_SECRET_B64" | base64 -d)"
REDIS_PW="$(printf %s "${REDIS_PW_B64:-}" | base64 -d 2>/dev/null || true)"
GHCR_TOKEN="$(printf %s "${GHCR_TOKEN_B64:-}" | base64 -d 2>/dev/null || true)"
DEPLOY_MODE="${DEPLOY_MODE:-build}"; IMAGE="${IMAGE:-}"; GHCR_USER="${GHCR_USER:-token}"; CONTAINER="${CONTAINER:-customer360-tracking-api}"
REPLICAS="${REPLICAS:-1}"; LB_IMAGE="${LB_IMAGE:-nginx:alpine}"; NETWORK="${NETWORK:-c360-tracking}"; LB_NAME="customer360-tracking-lb"
RL_REQUESTS="${RL_REQUESTS:-}"; RL_WINDOW="${RL_WINDOW:-}"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  sudo systemctl enable --now docker
fi
# Reclaim disk before we write/pull anything. Each deploy pulls a new SHA-pinned image
# and the old ones pile up until a small VM fills its disk ("No space left on device"
# on the very first env-file write). This runs before any disk write (the heredoc streams
# over stdin) so it recovers even from an already-full disk. The currently-running
# tracking + LB containers still hold their images here, so `image prune -a` keeps them
# and drops only the stale ones. Best-effort: never fail the deploy on cleanup.
if command -v docker >/dev/null 2>&1; then
  echo "   reclaiming disk (df before): $(df -h --output=avail / | tail -1 | tr -d ' ') free"
  sudo docker container prune -f  >/dev/null 2>&1 || true
  sudo docker image prune -a -f   >/dev/null 2>&1 || true
  sudo docker builder prune -a -f >/dev/null 2>&1 || true
  echo "   reclaiming disk (df after):  $(df -h --output=avail / | tail -1 | tr -d ' ') free"
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
if [ -n "${RL_REQUESTS:-}" ]; then
  echo "TRACKING_RATE_LIMIT_REQUESTS=$RL_REQUESTS" >> "$env_file"
  [ -n "${RL_WINDOW:-}" ] && echo "TRACKING_RATE_LIMIT_WINDOW_SECONDS=$RL_WINDOW" >> "$env_file"
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
echo "   pulling LB image $LB_IMAGE ..."
sudo docker pull "$LB_IMAGE" >/dev/null

# Private bridge so the LB reaches replicas by container name; replicas need NO host ports
# and keep their built-in :8010 HEALTHCHECK (each listens on :8010 in its own namespace).
# Outbound to S3/Redis/OTLP still works from the bridge via NAT (source IP = this box).
sudo docker network inspect "$NETWORK" >/dev/null 2>&1 || sudo docker network create "$NETWORK" >/dev/null

# Clean slate: drop the legacy single container (migration from --network host), any prior
# replicas (also handles a REPLICAS decrease, e.g. prod 5 -> 3), and the old LB.
sudo docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
for c in $(sudo docker ps -aq --filter "name=${CONTAINER}-"); do sudo docker rm -f "$c" >/dev/null 2>&1 || true; done
sudo docker rm -f "$LB_NAME" >/dev/null 2>&1 || true

# Cap every container's logs. The default json-file driver grows UNBOUNDED, and this is a
# high-volume ingestion path (the nginx LB logs one line per event, plus N app replicas),
# so without rotation /var/lib/docker/containers/*/*-json.log fills the VM disk. 10m x 3
# bounds each container to ~30 MB. Applied (below) to the replicas and the LB.
LOG_OPTS="--log-opt max-size=10m --log-opt max-file=3"

# Start N app replicas on the bridge and build the nginx upstream list from their names.
echo "   starting $REPLICAS replica(s) ..."
upstreams=""
i=1
while [ "$i" -le "$REPLICAS" ]; do
  name="${CONTAINER}-${i}"
  sudo docker run -d --name "$name" --restart unless-stopped $LOG_OPTS \
    --network "$NETWORK" --env-file /opt/c360/tracking.env "$RUN_IMG" >/dev/null
  upstreams="${upstreams}    server ${name}:8010 max_fails=3 fail_timeout=10s;\n"
  i=$((i+1))
done

# Generate the nginx least_conn round-robin config (retries the next replica on failure) and
# start the LB on host :8010 — the single address Caddy's DATA_UPSTREAM already points at.
lb_conf="$(mktemp)"
printf 'upstream tracking_backends {\n    least_conn;\n%b}\n\n' "$upstreams" > "$lb_conf"
cat >> "$lb_conf" <<'NGINX'
server {
    listen 8010;
    # LB self-check (via admin tunnel): curl http://localhost:8010/lb-health
    location = /lb-health { default_type text/plain; return 200 "ok\n"; }
    location / {
        proxy_pass http://tracking_backends;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_next_upstream error timeout http_502 http_503 http_504;
    }
}
NGINX
sudo mv "$lb_conf" /opt/c360/tracking-lb.conf
sudo chmod 644 /opt/c360/tracking-lb.conf
sudo docker run -d --name "$LB_NAME" --restart unless-stopped $LOG_OPTS \
  --network "$NETWORK" -p 8010:8010 \
  -v /opt/c360/tracking-lb.conf:/etc/nginx/conf.d/default.conf:ro "$LB_IMAGE" >/dev/null

sleep 3
echo "   --- app replicas ($REPLICAS) ---"
sudo docker ps --filter "name=${CONTAINER}-" --format '   {{.Names}} ({{.Status}}) image={{.Image}}'
echo "   --- load balancer (host :8010) ---"
sudo docker ps --filter "name=${LB_NAME}" --format '   {{.Names}} ({{.Status}}) image={{.Image}}'
REMOTE

echo ">> Done. $REPLICAS data-tracking-api replica(s) behind the local nginx LB on :8010 (health: /health)."
echo "   Public (Caddy /data + LB): https://beta.leocdp.com/data/api/v1/tracking/logs"
echo "   Direct (admin tunnel): ssh -i $SSH_KEY -L 8010:localhost:8010 $BASTION  # http://localhost:8010/health (via LB), /lb-health"

# --- release ledger: record this deploy to the GitHub Deployments API (best-effort) ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/record_deploy.sh"
record_deployment "$ENV" "$SERVICE" "${IMAGE:-local}" success
