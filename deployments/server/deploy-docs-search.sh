#!/usr/bin/env bash
# Deploy docs-vector-search (provider-agnostic RAG, FastAPI :8001) onto its OWN "docs" server VM
# and refresh the pgvector index on the shared vDB.
#   ./deploy-docs-search.sh <uat|prod>
#
# OpenAI is the default; Gemini and local fastembed/Qwen can be selected independently. Vectors
# live in pgvector on the shared vDB
# (schema "rag"), off the app box. This is the CD path: it PULLS the CI-built image from GHCR
# (set BUILD_LOCAL=1 to build on the VM from source). enrich (chunk -> embed -> upsert) runs
# ON the box, where the vDB + model weights live; it also creates the rag schema + pgvector
# extension idempotently, so no separate SQL bootstrap is needed.
#
# Target box = servers["$DOCS_SERVER_KEY"] (default "docs"), defined in overlays/<env>.tfvars
# and provisioned by this module's deploy.sh (apply). Overrides (env):
#   BASTION_USER / SSH_KEY / DOCS_SERVER_KEY / DOCS_PORT / IMAGE_TAG / BUILD_LOCAL /
#   DOCS_IMAGE_TARGET (hosted|local; used with BUILD_LOCAL=1)
#   DOCS_EMBEDDING_PROVIDER / DOCS_LLM_PROVIDER / DOCS_*_EMBEDDING_* /
#   DOCS_*_LLM_* / DOCS_RERANK_ENABLED / DOCS_RERANK_MODEL
#   DOCS_PG_SCHEMA / DOCS_GGUF_URL
#   DOCS_REDIS_HOST / DOCS_REDIS_PORT / DOCS_REDIS_DB
#   DOCS_REDIS_IMAGE (optional, default redis:7-alpine)
# DB creds come from ../postgres (outputs + TF_VAR_db_password). For local docker compose dev
# instead, see tools/docs-vector-search/docker-compose.yml + .env.example.
set -euo pipefail
cd "$(dirname "$0")"           # deployments/server
REPO_ROOT="$(cd ../.. && pwd)" # repo root (contains docs/ and tools/docs-vector-search/)

# ACTION:
#   deploy  (default) pull the image, ship the corpus, enrich, (re)start the server.
#   reindex docs-only refresh: ship the corpus and run enrich with the image the RUNNING
#           container already uses — no pull, no restart (the server reads pgvector per
#           request). Used by docs-vector-index.yml via the CD `docs-index` step.
#   destroy remove the containers.
ENV="${1:-}"; ACTION="${2:-deploy}"
case "$ENV" in uat | prod) ;; *) echo "Usage: ./deploy-docs-search.sh <uat|prod> [deploy|reindex|destroy]"; exit 1 ;; esac
case "$ACTION" in deploy | reindex | destroy) ;; *) echo "Usage: ./deploy-docs-search.sh <uat|prod> [deploy|reindex|destroy]"; exit 1 ;; esac

# Keep CI-injected provider secrets authoritative if a developer's optional local
# deployments/server/.env also exists on the runner.
_DOCS_OPENAI_API_KEY_FROM_ENV="${DOCS_OPENAI_API_KEY:-}"
[[ -f .env ]] && { set -a; source ./.env; set +a; }
if [[ -n "$_DOCS_OPENAI_API_KEY_FROM_ENV" ]]; then
  DOCS_OPENAI_API_KEY="$_DOCS_OPENAI_API_KEY_FROM_ENV"
fi
unset _DOCS_OPENAI_API_KEY_FROM_ENV
SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"
DOCS_SERVER_KEY="${DOCS_SERVER_KEY:-docs}"
DOCS_PORT="${DOCS_PORT:-8001}"
DOCS_PG_SCHEMA="${DOCS_PG_SCHEMA:-rag}"
DOCS_EMBEDDING_PROVIDER="${DOCS_EMBEDDING_PROVIDER:-openai}"
DOCS_RERANK_ENABLED="${DOCS_RERANK_ENABLED:-true}"
DOCS_ENRICH_BATCH_SIZE="${DOCS_ENRICH_BATCH_SIZE:-128}"
DOCS_RERANK_PROVIDER="${DOCS_RERANK_PROVIDER:-openai}"
DOCS_RERANK_MODEL="${DOCS_RERANK_MODEL:-BAAI/bge-reranker-base}"
DOCS_OPENAI_RERANK_TIMEOUT_SECONDS="${DOCS_OPENAI_RERANK_TIMEOUT_SECONDS:-8}"
DOCS_RERANK_OPENAI_FALLBACK="${DOCS_RERANK_OPENAI_FALLBACK:-vector}"
DOCS_LLM_PROVIDER="${DOCS_LLM_PROVIDER:-openai}"
DOCS_HOSTED_LLM_MAX_OUTPUT_TOKENS="${DOCS_HOSTED_LLM_MAX_OUTPUT_TOKENS:-1024}"
DOCS_LOCAL_LLM_MAX_OUTPUT_TOKENS="${DOCS_LOCAL_LLM_MAX_OUTPUT_TOKENS:-512}"
DOCS_OPENAI_API_KEY="${DOCS_OPENAI_API_KEY:-}"
DOCS_OPENAI_API_BASE_URL="${DOCS_OPENAI_API_BASE_URL:-https://api.openai.com/v1}"
DOCS_OPENAI_REQUEST_TIMEOUT_SECONDS="${DOCS_OPENAI_REQUEST_TIMEOUT_SECONDS:-120}"
DOCS_OPENAI_EMBEDDING_MODEL="${DOCS_OPENAI_EMBEDDING_MODEL:-text-embedding-3-small}"
DOCS_OPENAI_EMBEDDING_DIMENSIONS="${DOCS_OPENAI_EMBEDDING_DIMENSIONS:-384}"
DOCS_OPENAI_RERANK_MODEL="${DOCS_OPENAI_RERANK_MODEL:-gpt-5-nano}"
DOCS_OPENAI_LLM_MODEL="${DOCS_OPENAI_LLM_MODEL:-gpt-5.6-luna}"
RETRIEVE_TOP_N="${RETRIEVE_TOP_N:-40}"
RERANK_CANDIDATES="${RERANK_CANDIDATES:-40}"
RERANK_TOP_K="${RERANK_TOP_K:-8}"
FINAL_CONTEXT_TOP_K="${FINAL_CONTEXT_TOP_K:-5}"
DOCS_GEMINI_API_KEY="${DOCS_GEMINI_API_KEY:-}"
DOCS_GEMINI_API_BASE_URL="${DOCS_GEMINI_API_BASE_URL:-https://generativelanguage.googleapis.com/v1beta}"
DOCS_GEMINI_REQUEST_TIMEOUT_SECONDS="${DOCS_GEMINI_REQUEST_TIMEOUT_SECONDS:-120}"
DOCS_GEMINI_EMBEDDING_MODEL="${DOCS_GEMINI_EMBEDDING_MODEL:-gemini-embedding-001}"
DOCS_GEMINI_EMBEDDING_DIMENSIONS="${DOCS_GEMINI_EMBEDDING_DIMENSIONS:-384}"
DOCS_GEMINI_LLM_MODEL="${DOCS_GEMINI_LLM_MODEL:-gemini-2.5-flash}"
DOCS_LOCAL_EMBEDDING_MODEL="${DOCS_LOCAL_EMBEDDING_MODEL:-sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2}"
DOCS_LOCAL_EMBEDDING_DIMENSIONS="${DOCS_LOCAL_EMBEDDING_DIMENSIONS:-384}"
DOCS_LOCAL_LLM_MODEL_PATH="${DOCS_LOCAL_LLM_MODEL_PATH:-/app/models/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf}"
DOCS_LOCAL_LLM_CONTEXT_TOKENS="${DOCS_LOCAL_LLM_CONTEXT_TOKENS:-2048}"
DOCS_LOCAL_LLM_THREADS="${DOCS_LOCAL_LLM_THREADS:-2}"
DOCS_LOCAL_LLM_BATCH_SIZE="${DOCS_LOCAL_LLM_BATCH_SIZE:-512}"
DOCS_LOCAL_LLM_GPU_LAYERS="${DOCS_LOCAL_LLM_GPU_LAYERS:--1}"
DOCS_IMAGE_TARGET="${DOCS_IMAGE_TARGET:-}"
if [[ -z "$DOCS_IMAGE_TARGET" ]]; then
  case "$DOCS_EMBEDDING_PROVIDER:$DOCS_RERANK_PROVIDER:$DOCS_RERANK_OPENAI_FALLBACK:$DOCS_LLM_PROVIDER" in
    *local*) DOCS_IMAGE_TARGET="local" ;;
    *) DOCS_IMAGE_TARGET="hosted" ;;
  esac
fi
case "$DOCS_IMAGE_TARGET" in
  hosted | local) ;;
  *) echo "ERROR: DOCS_IMAGE_TARGET must be 'hosted' or 'local'."; exit 1 ;;
esac
if [[ "$DOCS_IMAGE_TARGET" == "hosted" ]]; then
  case "$DOCS_EMBEDDING_PROVIDER:$DOCS_RERANK_PROVIDER:$DOCS_RERANK_OPENAI_FALLBACK:$DOCS_LLM_PROVIDER" in
    *local*) echo "ERROR: Local providers require DOCS_IMAGE_TARGET=local."; exit 1 ;;
  esac
fi
DOCS_REDIS_HOST="${DOCS_REDIS_HOST:-127.0.0.1}"
DOCS_REDIS_PORT="${DOCS_REDIS_PORT:-6580}"
DOCS_REDIS_DB="${DOCS_REDIS_DB:-0}"
DOCS_REDIS_PASSWORD="${DOCS_REDIS_PASSWORD:-}"
DOCS_REDIS_CONNECT_TIMEOUT_SECONDS="${DOCS_REDIS_CONNECT_TIMEOUT_SECONDS:-1}"
DOCS_REDIS_SOCKET_TIMEOUT_SECONDS="${DOCS_REDIS_SOCKET_TIMEOUT_SECONDS:-1}"
# CORS origins for browsers hitting the API directly (the static docs site on GitHub Pages).
DOCS_CORS_ORIGINS="${DOCS_CORS_ORIGINS:-https://leo-cdp.github.io}"
# Per-IP /ask rate limit for public callers (via Caddy/XFF). Tune per env; 0 disables.
DOCS_ASK_RATE_MAX="${DOCS_ASK_RATE_MAX:-10}"
DOCS_ASK_RATE_WINDOW_SEC="${DOCS_ASK_RATE_WINDOW_SEC:-60}"
# Shared secret that lets the docs service treat the customer360-frontend /ai proxy as an internal
# caller (exempt from the public rate limit). The default is leoragbot; override it for production:
# admin AI traffic is rate-limited like any client. Set the SAME value as the frontend deploy's
# DOCS_INTERNAL_AUTH_SECRET — export it once before deploying, put it in both
# deployments/{server,frontend}/.env, or provide one CI secret to both jobs.
DOCS_INTERNAL_AUTH_SECRET="${DOCS_INTERNAL_AUTH_SECRET:-${DOCS_INTERNAL_SECRET:-leoragbot}}"
# Trusted reverse-proxy hops that append X-Forwarded-For (Caddy/LB in front = 1).
DOCS_TRUSTED_PROXY_HOPS="${DOCS_TRUSTED_PROXY_HOPS:-1}"
DOCS_GGUF_URL="${DOCS_GGUF_URL:-https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf}"
GGUF_NAME="Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"

. "$(cd "$(dirname "$0")/.." && pwd)/lib/tfvars.sh"   # tfval + srv_ip

# --- resolve the target VM by map key from THIS module's outputs ---
terraform workspace select "$ENV" >/dev/null 2>&1 || { echo "ERROR: no '$ENV' server workspace — deploy the server first."; exit 1; }
SERVERS_JSON="$(terraform output -json servers 2>/dev/null || true)"
[[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no servers output."; exit 1; }
FIP="$(srv_ip "$DOCS_SERVER_KEY" floating_ip)"
# The 'docs' box is provisioned by this module (overlays servers map). If it isn't there yet,
# SKIP rather than fail — so CD stays green until the box is applied; the next run picks it up.
if [[ -z "$FIP" ]]; then
  echo "::warning::docs-search SKIPPED — no floating IP for server key '$DOCS_SERVER_KEY' in '$ENV'. Add a '$DOCS_SERVER_KEY' box to overlays/$ENV.tfvars (servers, attach_floating) and apply this module; CD will deploy it on the next run."
  exit 0
fi
BASTION="${BASTION_USER:-leocdp360}@$FIP"
# ServerAlive* keeps the long enrich SSH session (minutes of embedding on 1 vCPU, no
# data flowing) from idle-dropping — a bare session exits 255 from a CI runner mid-enrich.
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
          -o ServerAliveInterval=30 -o ServerAliveCountMax=20)

CONTAINER="customer360-docs-vector-search"
REDIS_CONTAINER="customer360-docs-rate-limit-redis"
REDIS_IMAGE="${DOCS_REDIS_IMAGE:-redis:7-alpine}"
if [[ "$ACTION" == "destroy" ]]; then
  echo ">> Removing $CONTAINER and $REDIS_CONTAINER on $BASTION ..."
  ssh "${SSH_OPTS[@]}" "$BASTION" "sudo docker rm -f $CONTAINER $REDIS_CONTAINER >/dev/null 2>&1 || true; echo '   removed'"
  exit 0
fi

# --- ship the corpus so enrich can chunk + embed it on the box ---
# Only *.md is indexed (src.corpus ignores every other file), so ship just the markdown:
# docs/ is ~65 MB with images/PDFs/excalidraw but only ~1.3 MB of .md — shipping the
# whole tree was ~1.5 min of every deploy. Paths stay relative to docs/, as before.
ship_corpus() {
  echo ">> Shipping docs/ corpus (*.md only) ..."
  ( cd "$REPO_ROOT/docs" && find . -type f -name '*.md' -print0 | tar --null -T - -czf - ) \
    | ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo mkdir -p /opt/c360/docs-vector-search && sudo chown -R "$(id -un)" /opt/c360/docs-vector-search && sudo rm -rf /opt/c360/docs-vector-search/corpus /opt/c360/docs-vector-search/docs && mkdir -p /opt/c360/docs-vector-search/corpus && tar -C /opt/c360/docs-vector-search/corpus -xzf -'
}

# --- docs-only refresh: re-embed with the image already running, no pull / restart ---
if [[ "$ACTION" == "reindex" ]]; then
  ship_corpus
  echo ">> Re-indexing with the running container's image (no pull, no restart) ..."
  if ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$CONTAINER" <<'REMOTE'
set -euo pipefail
CONTAINER="$1"
img="$(sudo docker inspect -f '{{.Config.Image}}' "$CONTAINER" 2>/dev/null || true)"
if [ -z "$img" ] || [ ! -f /opt/c360/docs-vector-search.env ]; then
  echo "   no running $CONTAINER (or no env file) on this box — a full deploy is needed" >&2
  exit 3
fi
echo "   enrich with $img"
sudo docker run --rm --network host --env-file /opt/c360/docs-vector-search.env \
  -v /opt/c360/docs-vector-search/corpus:/app/corpus:ro -v /opt/c360/docs-models:/app/models \
  "$img" python -m src.enrich
REMOTE
  then
    echo ">> Done. Index refreshed (/health 'loaded_chunks' updates on the next restart)."
    exit 0
  else
    rc=$?
    [[ "$rc" -eq 3 ]] || exit "$rc"
    echo ">> Falling back to a full deploy."
    ACTION="deploy"
  fi
fi

# --- DB connection from the postgres deployment (same vDB; dedicated 'rag' schema) ---
pg="../postgres"
DB_NAME="$(tfval db_name "$pg/overlays/$ENV.tfvars")"
DB_USER="$(tfval db_username "$pg/overlays/$ENV.tfvars")"
DB_PASS="${TF_VAR_db_password:-$(tfval db_password "$pg/terraform.tfvars")}"
DB_HOST="$( (cd "$pg" && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -raw db_host 2>/dev/null) || true )"
DB_PORT="$( (cd "$pg" && terraform output -raw db_port 2>/dev/null) || echo 5432 )"
: "${DB_NAME:?missing db_name}"; : "${DB_USER:?missing db_username}"; : "${DB_PASS:?missing db_password}"; : "${DB_HOST:?could not read db_host from ../postgres outputs}"

echo ">> Target (docs): $BASTION :$DOCS_PORT   vDB: ${DB_NAME}.${DOCS_PG_SCHEMA}@${DB_HOST}:${DB_PORT}   embedding=$DOCS_EMBEDDING_PROVIDER   rerank=$DOCS_RERANK_ENABLED   llm=$DOCS_LLM_PROVIDER"

# --- CD image source: pull the CI-built image from GHCR by default; BUILD_LOCAL=1 ships
#     tools/docs-vector-search and builds on the VM (slow: llama-cpp-python). ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/ghcr.sh"
SERVICE="docs-vector-search"
GHCR_USER="${GHCR_USER:-${GITHUB_ACTOR:-token}}"
GHCR_TOKEN="${GHCR_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ "${BUILD_LOCAL:-0}" == "1" ]]; then
  DEPLOY_MODE="build"; IMAGE=""
  echo ">> Image: BUILD_LOCAL=1 — building $SERVICE on the VM from source (slow)."
  echo ">> Shipping tools/docs-vector-search/ ..."
  tar -C "$REPO_ROOT" -czf - tools/docs-vector-search \
    | ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo mkdir -p /opt/c360 && sudo chown "$(id -un)" /opt/c360 && tar -C /opt/c360 -xzf -'
else
  DEPLOY_MODE="ghcr"
  IMAGE="$(image_ref "$SERVICE" "$(resolve_tag "overlays/$ENV.tfvars")")"
  echo ">> Image: $IMAGE   (pull from GHCR; BUILD_LOCAL=1 to build on the VM)"
fi
if [[ "$DEPLOY_MODE" == "ghcr" && "$DOCS_IMAGE_TARGET" == "local" ]]; then
  echo "ERROR: CI publishes the hosted docs-search image only. Use BUILD_LOCAL=1 for local providers."
  exit 1
fi

ship_corpus

# OpenTelemetry (OTLP -> Jaeger) zero-code tracing lines. The docs box is dedicated (Jaeger is
# NOT co-located on it), so point OTLP at the monitoring/api box's private fixed IP. UAT defaults
# to disabled per policy in lib/otel.sh — deploy with OTEL_ENABLED=true to profile on demand.
. "$(cd "$(dirname "$0")/.." && pwd)/lib/otel.sh"
MON_SERVER_KEY="${MON_SERVER_KEY:-api}"
JAEGER_HOST="$(srv_ip "$MON_SERVER_KEY" fixed_ip)"; JAEGER_HOST="${JAEGER_HOST:-127.0.0.1}"
OTEL_LINES="$(otel_env_lines "$SERVICE" "$ENV" "$JAEGER_HOST")"

# env file built locally, shipped base64 (dodges ssh arg-flattening).
ENVB64="$(printf '%s' "PG_HOST=$DB_HOST
PG_PORT=$DB_PORT
PG_DATABASE=$DB_NAME
PG_USER=$DB_USER
PG_PASSWORD=$DB_PASS
PG_SCHEMA=$DOCS_PG_SCHEMA
CORPUS_DIR=/app/corpus
MODELS_DIR=/app/models
DOCS_EMBEDDING_PROVIDER=$DOCS_EMBEDDING_PROVIDER
DOCS_RERANK_ENABLED=$DOCS_RERANK_ENABLED
DOCS_RERANK_PROVIDER=$DOCS_RERANK_PROVIDER
DOCS_RERANK_MODEL=$DOCS_RERANK_MODEL
DOCS_OPENAI_RERANK_TIMEOUT_SECONDS=$DOCS_OPENAI_RERANK_TIMEOUT_SECONDS
DOCS_RERANK_OPENAI_FALLBACK=$DOCS_RERANK_OPENAI_FALLBACK
DOCS_LLM_PROVIDER=$DOCS_LLM_PROVIDER
DOCS_HOSTED_LLM_MAX_OUTPUT_TOKENS=$DOCS_HOSTED_LLM_MAX_OUTPUT_TOKENS
DOCS_LOCAL_LLM_MAX_OUTPUT_TOKENS=$DOCS_LOCAL_LLM_MAX_OUTPUT_TOKENS
DOCS_OPENAI_API_KEY=$DOCS_OPENAI_API_KEY
DOCS_OPENAI_API_BASE_URL=$DOCS_OPENAI_API_BASE_URL
DOCS_OPENAI_REQUEST_TIMEOUT_SECONDS=$DOCS_OPENAI_REQUEST_TIMEOUT_SECONDS
DOCS_OPENAI_EMBEDDING_MODEL=$DOCS_OPENAI_EMBEDDING_MODEL
DOCS_OPENAI_EMBEDDING_DIMENSIONS=$DOCS_OPENAI_EMBEDDING_DIMENSIONS
DOCS_OPENAI_RERANK_MODEL=$DOCS_OPENAI_RERANK_MODEL
DOCS_OPENAI_LLM_MODEL=$DOCS_OPENAI_LLM_MODEL
RETRIEVE_TOP_N=$RETRIEVE_TOP_N
RERANK_CANDIDATES=$RERANK_CANDIDATES
RERANK_TOP_K=$RERANK_TOP_K
FINAL_CONTEXT_TOP_K=$FINAL_CONTEXT_TOP_K
DOCS_GEMINI_API_KEY=$DOCS_GEMINI_API_KEY
DOCS_GEMINI_API_BASE_URL=$DOCS_GEMINI_API_BASE_URL
DOCS_GEMINI_REQUEST_TIMEOUT_SECONDS=$DOCS_GEMINI_REQUEST_TIMEOUT_SECONDS
DOCS_GEMINI_EMBEDDING_MODEL=$DOCS_GEMINI_EMBEDDING_MODEL
DOCS_GEMINI_EMBEDDING_DIMENSIONS=$DOCS_GEMINI_EMBEDDING_DIMENSIONS
DOCS_GEMINI_LLM_MODEL=$DOCS_GEMINI_LLM_MODEL
DOCS_LOCAL_EMBEDDING_MODEL=$DOCS_LOCAL_EMBEDDING_MODEL
DOCS_LOCAL_EMBEDDING_DIMENSIONS=$DOCS_LOCAL_EMBEDDING_DIMENSIONS
DOCS_LOCAL_LLM_MODEL_PATH=$DOCS_LOCAL_LLM_MODEL_PATH
DOCS_LOCAL_LLM_CONTEXT_TOKENS=$DOCS_LOCAL_LLM_CONTEXT_TOKENS
DOCS_LOCAL_LLM_THREADS=$DOCS_LOCAL_LLM_THREADS
DOCS_LOCAL_LLM_BATCH_SIZE=$DOCS_LOCAL_LLM_BATCH_SIZE
DOCS_LOCAL_LLM_GPU_LAYERS=$DOCS_LOCAL_LLM_GPU_LAYERS
DOCS_REDIS_HOST=$DOCS_REDIS_HOST
DOCS_REDIS_PORT=$DOCS_REDIS_PORT
DOCS_REDIS_DB=$DOCS_REDIS_DB
DOCS_REDIS_PASSWORD=$DOCS_REDIS_PASSWORD
DOCS_REDIS_CONNECT_TIMEOUT_SECONDS=$DOCS_REDIS_CONNECT_TIMEOUT_SECONDS
DOCS_REDIS_SOCKET_TIMEOUT_SECONDS=$DOCS_REDIS_SOCKET_TIMEOUT_SECONDS
CORS_ORIGINS=$DOCS_CORS_ORIGINS
ASK_RATE_MAX=$DOCS_ASK_RATE_MAX
ASK_RATE_WINDOW_SEC=$DOCS_ASK_RATE_WINDOW_SEC
INTERNAL_API_SECRET=$DOCS_INTERNAL_AUTH_SECRET
TRUSTED_PROXY_HOPS=$DOCS_TRUSTED_PROXY_HOPS
DOCS_ENRICH_BATCH_SIZE=$DOCS_ENRICH_BATCH_SIZE
$OTEL_LINES" | base64 | tr -d '\n')"

echo ">> Fetching the model, refreshing the index (enrich), and (re)starting the container ..."
ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' \
  "$DOCS_PORT" "$ENVB64" "$DOCS_GGUF_URL" "$GGUF_NAME" "$DEPLOY_MODE" "$IMAGE" \
  "$GHCR_USER" "$(printf %s "$GHCR_TOKEN" | base64 | tr -d '\n')" "$CONTAINER" \
  "$REDIS_CONTAINER" "$REDIS_IMAGE" < <(declare -f docker_pull_retry; cat <<'REMOTE'
set -euo pipefail
PORT="$1"; ENVB64="$2"; GGUF_URL="$3"; GGUF_NAME="$4"; DEPLOY_MODE="${5:-ghcr}"; IMAGE="${6:-}"
GHCR_USER="${7:-token}"; GHCR_TOKEN="$(printf %s "${8:-}" | base64 -d 2>/dev/null || true)"; CONTAINER="${9:-customer360-docs-vector-search}"
REDIS_CONTAINER="${10:-customer360-docs-rate-limit-redis}"
REDIS_IMAGE="${11:-redis:7-alpine}"

command -v docker >/dev/null 2>&1 || { sudo apt-get update -qq; sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io; sudo systemctl enable --now docker; }
command -v curl   >/dev/null 2>&1 || { sudo apt-get update -qq; sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl; }

# 2 GB is tight for e5 + reranker + Qwen (~1.4 GB resident). Add a 2 GB swapfile once so a
# transient spike can't OOM-kill the server. Idempotent + best-effort.
if [ -z "$(swapon --show 2>/dev/null)" ] && [ ! -f /swapfile ]; then
  echo "   adding 2G swapfile ..."
  sudo fallocate -l 2G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  sudo chmod 600 /swapfile; sudo mkswap /swapfile >/dev/null; sudo swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# Reclaim disk before model/image operations: drop stale images so pulls can't fail with
# "No space left on device". The RUNNING docs container is kept: `image prune -a` spares
# the image it uses, so the new image's unchanged layers (python base + deps — only src/
# changes on most commits) are already on disk and the pull fetches only the delta.
# Removing the container first (the previous behaviour) wiped every layer, so each deploy
# re-downloaded the whole image (~1 min) with the chatbot down meanwhile. A failed pull
# falls back to the full cleanup below.
reclaim_disk() {
  echo "   reclaiming disk (df before): $(df -h --output=avail / | tail -1 | tr -d ' ') free"
  sudo docker container prune -f >/dev/null 2>&1 || true
  sudo docker image prune -a -f   >/dev/null 2>&1 || true
  sudo docker builder prune -a -f >/dev/null 2>&1 || true
  echo "   reclaiming disk (df after):  $(df -h --output=avail / | tail -1 | tr -d ' ') free"
}
if command -v docker >/dev/null 2>&1; then
  reclaim_disk
  if [ "$(df --output=avail / | tail -1 | tr -d ' ')" -lt 1572864 ]; then   # < 1.5 GiB
    echo "   low disk — removing the running docs container before the pull"
    sudo docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; reclaim_disk
  fi
fi

MODELS_DIR=/opt/c360/docs-models
CORPUS_DIR=/opt/c360/docs-vector-search/corpus
sudo mkdir -p "$MODELS_DIR"; sudo chown "$(id -un)" "$MODELS_DIR"

umask 077; env_file="$(mktemp)"; printf '%s' "$ENVB64" | base64 -d > "$env_file"
sudo mkdir -p /opt/c360; sudo mv "$env_file" /opt/c360/docs-vector-search.env; sudo chmod 600 /opt/c360/docs-vector-search.env

redis_host="$(awk -F= '$1=="DOCS_REDIS_HOST"{print $2}' /opt/c360/docs-vector-search.env | tail -1)"
redis_port="$(awk -F= '$1=="DOCS_REDIS_PORT"{print $2}' /opt/c360/docs-vector-search.env | tail -1)"
redis_host="${redis_host:-127.0.0.1}"
redis_port="${redis_port:-6580}"
if [ "$redis_host" = "127.0.0.1" ] || [ "$redis_host" = "localhost" ]; then
  # Already running with the same image + port? Leave it (it only holds rate-limit counters).
  if [ "$(sudo docker inspect -f '{{.State.Running}} {{.Config.Image}} {{join .Args " "}}' "$REDIS_CONTAINER" 2>/dev/null)" = "true $REDIS_IMAGE redis-server --port $redis_port --save  --appendonly no" ]; then
    echo "   local Redis for rate limiting already running (${redis_host}:${redis_port})"
  else
    echo "   starting local Redis for rate limiting (${redis_host}:${redis_port}, no auth) ..."
    sudo docker rm -f "$REDIS_CONTAINER" >/dev/null 2>&1 || true
    sudo docker pull "$REDIS_IMAGE" >/dev/null
    sudo docker run -d --name "$REDIS_CONTAINER" --restart unless-stopped \
      --network host --log-opt max-size=10m --log-opt max-file=3 \
      "$REDIS_IMAGE" redis-server --port "$redis_port" --save "" --appendonly no >/dev/null
  fi
else
  echo "   using external Redis for rate limiting at ${redis_host}:${redis_port}"
fi

# Qwen is optional. Fetch its GGUF only when local generation is selected; hosted
# providers should not require a multi-hundred-megabyte local model download.
if grep -qx 'DOCS_LLM_PROVIDER=local' /opt/c360/docs-vector-search.env; then
  if [ ! -s "$MODELS_DIR/$GGUF_NAME" ]; then
    echo "   fetching $GGUF_NAME ..."
    curl -fL --retry 3 -o "$MODELS_DIR/$GGUF_NAME.part" "$GGUF_URL"
    mv "$MODELS_DIR/$GGUF_NAME.part" "$MODELS_DIR/$GGUF_NAME"
  else
    echo "   model present: $GGUF_NAME"
  fi
else
  echo "   skipping Qwen GGUF (DOCS_LLM_PROVIDER is not local)"
fi

if [ "$DEPLOY_MODE" = "ghcr" ]; then
  echo "   pulling $IMAGE ..."
  [ -n "$GHCR_TOKEN" ] && printf %s "$GHCR_TOKEN" | sudo docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
  if ! sudo docker pull "$IMAGE"; then
    echo "   pull failed with the old container still up — freeing its layers and retrying"
    sudo docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; reclaim_disk
    docker_pull_retry "$IMAGE"
  fi
  RUN_IMG="$IMAGE"
else
  sed -i 's/ --mount=[^ ]*//g' /opt/c360/tools/docs-vector-search/Dockerfile 2>/dev/null || true
  sudo docker build --target "$DOCS_IMAGE_TARGET" \
    -t customer360-docs-vector-search-local /opt/c360/tools/docs-vector-search
  RUN_IMG="customer360-docs-vector-search-local"
fi

VOLS=(-v "$CORPUS_DIR:/app/corpus:ro" -v "$MODELS_DIR:/app/models")

# refresh the index first (chunk -> embed -> upsert into pgvector; creates the rag schema
# idempotently). --rm so it never lingers holding RAM alongside the server. With hosted
# providers enrich is light, so the old server keeps serving meanwhile and is swapped only
# below; local models (~1.4 GB) can't fit twice on this box, so stop the old server first.
if grep -Eq '^DOCS_(EMBEDDING|LLM|RERANK)_PROVIDER=local$' /opt/c360/docs-vector-search.env; then
  sudo docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
fi
echo "   enrich: building/refreshing the pgvector index ..."
sudo docker run --rm --network host --env-file /opt/c360/docs-vector-search.env "${VOLS[@]}" "$RUN_IMG" python -m src.enrich

# then serve (image default CMD: uvicorn src.server:app --port 8001)
sudo docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
sudo docker run -d --name "$CONTAINER" --restart unless-stopped --network host \
  --env-file /opt/c360/docs-vector-search.env "${VOLS[@]}" "$RUN_IMG"
echo "   waiting for health check (:$PORT/health, up to 30s) ..."
for _i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "   health OK (:$PORT/health) after ${_i}s"; break
  fi
  [ "$_i" -eq 30 ] && echo "   WARN: health not ready after 30s (models load on first request)" || sleep 1
done
sudo docker ps --filter name="$CONTAINER" --format '   running: {{.Names}} ({{.Status}})'
# Drop the previous image now that nothing uses it (the next deploy's reclaim keeps this one).
sudo docker image prune -a -f >/dev/null 2>&1 || true
REMOTE
)
echo ">> Done. Expose via the LB (add a 'docs' backend -> <box-ip>:$DOCS_PORT) if it needs public access."

# --- release ledger: record this deploy to the GitHub Deployments API (best-effort) ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/record_deploy.sh"
record_deployment "$ENV" "$SERVICE" "${IMAGE:-local}" success
