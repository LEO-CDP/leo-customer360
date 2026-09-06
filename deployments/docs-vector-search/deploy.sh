#!/usr/bin/env bash
# Deploy docs-vector-search (local-model RAG, FastAPI :8000) onto its DEDICATED
# vServer and refresh the pgvector index on the shared vDB.
#
#   uat  -> dedicated box, server key "docs" (../server/overlays/uat.tfvars)
#   prod -> dedicated box, server key "docs" (../server/overlays/prod.tfvars)
#
#   ./deploy.sh <uat|prod>            # (re)deploy: pull image -> fetch model -> enrich -> serve
#   ./deploy.sh <uat|prod> destroy    # remove the container
#
# This is the CD path: it PULLS the CI-built image from GHCR (set BUILD_LOCAL=1 to
# build on the VM from source instead). enrich (chunk -> embed -> upsert) runs on
# the box because that is where the vDB and the model weights live; it also creates
# the `rag` schema + pgvector extension idempotently, so no separate SQL bootstrap
# is needed. The reranker + Qwen 0.5B make the resident set ~1.4 GB — the remote
# step adds a swapfile; set docs_rerank_enabled=false in the overlay to shed ~300 MB.
#
# DB creds come from ../postgres (outputs + TF_VAR_db_password). For local docker
# compose dev instead, see docker-compose.yml + .env.uat.example (not this script).
set -euo pipefail
cd "$(dirname "$0")"             # deployments/docs-vector-search
REPO_ROOT="$(cd ../.. && pwd)"  # repo root (contains docs/ and tools/docs-vector-search/)

ENV="${1:-}"; ACTION="${2:-deploy}"
case "$ENV" in uat | prod) ;; *) echo "Usage: ./deploy.sh <uat|prod> [deploy|destroy]"; exit 1 ;; esac

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
SERVER_KEY="${DOCS_SERVER_KEY:-$(tfval docs_server_key "$ovl")}"; SERVER_KEY="${SERVER_KEY:-docs}"
PORT="$(tfval docs_port "$ovl")"; PORT="${PORT:-8000}"
DB_SCHEMA="$(tfval docs_pg_schema "$ovl")"; DB_SCHEMA="${DB_SCHEMA:-rag}"
EMBED_MODEL="$(tfval docs_embed_model "$ovl")"; EMBED_MODEL="${EMBED_MODEL:-intfloat/multilingual-e5-small}"
EMBED_DIM="$(tfval docs_embed_dim "$ovl")"; EMBED_DIM="${EMBED_DIM:-384}"
RERANK_ENABLED="$(tfval docs_rerank_enabled "$ovl")"; RERANK_ENABLED="${RERANK_ENABLED:-true}"
RERANK_MODEL="$(tfval docs_rerank_model "$ovl")"; RERANK_MODEL="${RERANK_MODEL:-BAAI/bge-reranker-base}"
GGUF_URL="$(tfval docs_gguf_url "$ovl")"; GGUF_URL="${GGUF_URL:-https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf}"
GGUF_NAME="Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"

# --- resolve the target VM from ../server outputs ---
SERVERS_JSON="$( (cd ../server && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -json servers 2>/dev/null) || true )"
[[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no ../server servers output for $ENV — deploy the server first."; exit 1; }
FIP="$(printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get("floating_ip") for i in (s.get("internal_interfaces") or []) if i.get("floating_ip")), ""))' "$SERVER_KEY")"
# The 'docs' box is provisioned out-of-band (CD never runs infra). If it isn't there
# yet, SKIP rather than fail — otherwise a not-yet-provisioned box turns every CD run
# red. Same philosophy as the sso-realm step; the next deploy picks the box up.
if [[ -z "$FIP" ]]; then
  echo "::warning::docs-search SKIPPED — no floating IP for server key '$SERVER_KEY' in '$ENV'. Add a '$SERVER_KEY' box to ../server/overlays/$ENV.tfvars and apply it out-of-band; CD will deploy it on the next run."
  exit 0
fi
BASTION="${BASTION_USER:-leocdp360}@$FIP"
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)

CONTAINER="customer360-docs-vector-search"
if [[ "$ACTION" == "destroy" ]]; then
  echo ">> Removing $CONTAINER on $BASTION ..."
  ssh "${SSH_OPTS[@]}" "$BASTION" "sudo docker rm -f $CONTAINER >/dev/null 2>&1; echo '   removed'"
  exit 0
fi

# --- DB connection from the postgres deployment (same vDB; dedicated 'rag' schema) ---
pg="../postgres"
DB_NAME="$(tfval db_name "$pg/overlays/$ENV.tfvars")"
DB_USER="$(tfval db_username "$pg/overlays/$ENV.tfvars")"
DB_PASS="${TF_VAR_db_password:-$(tfval db_password "$pg/terraform.tfvars")}"
DB_HOST="$( (cd "$pg" && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -raw db_host 2>/dev/null) || true )"
DB_PORT="$( (cd "$pg" && terraform output -raw db_port 2>/dev/null) || echo 5432 )"
: "${DB_NAME:?missing db_name}"; : "${DB_USER:?missing db_username}"; : "${DB_PASS:?missing db_password}"; : "${DB_HOST:?could not read db_host from ../postgres outputs}"

echo ">> Target (docs): $BASTION :$PORT   vDB: ${DB_NAME}.${DB_SCHEMA}@${DB_HOST}:${DB_PORT}   rerank=$RERANK_ENABLED"

# --- CD image source: pull the CI-built image from GHCR by default; BUILD_LOCAL=1
#     ships tools/docs-vector-search and builds on the VM (slow: llama-cpp-python). ---
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

# --- ship the corpus (docs/**) so enrich can chunk + embed it on the box ---
echo ">> Shipping docs/ corpus ..."
tar -C "$REPO_ROOT" -czf - docs \
  | ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo mkdir -p /opt/c360/docs-vector-search && sudo chown "$(id -un)" /opt/c360/docs-vector-search && rm -rf /opt/c360/docs-vector-search/corpus && mkdir -p /opt/c360/docs-vector-search && tar -C /opt/c360/docs-vector-search -xzf - && mv /opt/c360/docs-vector-search/docs /opt/c360/docs-vector-search/corpus'

# env file built locally, shipped base64 (dodges ssh arg-flattening).
ENVB64="$(printf '%s' "PG_HOST=$DB_HOST
PG_PORT=$DB_PORT
PG_DATABASE=$DB_NAME
PG_USER=$DB_USER
PG_PASSWORD=$DB_PASS
PG_SCHEMA=$DB_SCHEMA
CORPUS_DIR=/app/corpus
MODELS_DIR=/app/models
EMBED_MODEL=$EMBED_MODEL
EMBED_DIM=$EMBED_DIM
RERANK_ENABLED=$RERANK_ENABLED
RERANK_MODEL=$RERANK_MODEL
QWEN_MODEL_PATH=/app/models/$GGUF_NAME" | base64 | tr -d '\n')"

echo ">> Fetching the model, refreshing the index (enrich), and (re)starting the container ..."
ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' \
  "$PORT" "$ENVB64" "$GGUF_URL" "$GGUF_NAME" "$DEPLOY_MODE" "$IMAGE" \
  "$GHCR_USER" "$(printf %s "$GHCR_TOKEN" | base64 | tr -d '\n')" "$CONTAINER" <<'REMOTE'
set -euo pipefail
PORT="$1"; ENVB64="$2"; GGUF_URL="$3"; GGUF_NAME="$4"; DEPLOY_MODE="${5:-ghcr}"; IMAGE="${6:-}"
GHCR_USER="${7:-token}"; GHCR_TOKEN="$(printf %s "${8:-}" | base64 -d 2>/dev/null || true)"; CONTAINER="${9:-customer360-docs-vector-search}"

command -v docker >/dev/null 2>&1 || { sudo apt-get update -qq; sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io; sudo systemctl enable --now docker; }
command -v curl   >/dev/null 2>&1 || { sudo apt-get update -qq; sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl; }

# 2 GB is tight for e5 + reranker + Qwen (~1.4 GB resident). Add a 2 GB swapfile
# once so a transient spike can't OOM-kill the server. Idempotent + best-effort.
if [ -z "$(swapon --show 2>/dev/null)" ] && [ ! -f /swapfile ]; then
  echo "   adding 2G swapfile ..."
  sudo fallocate -l 2G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  sudo chmod 600 /swapfile; sudo mkswap /swapfile >/dev/null; sudo swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

MODELS_DIR=/opt/c360/docs-models
CORPUS_DIR=/opt/c360/docs-vector-search/corpus
sudo mkdir -p "$MODELS_DIR"; sudo chown "$(id -un)" "$MODELS_DIR"

# generator weights: fetch once, reuse across deploys (fastembed ONNX self-downloads into $MODELS_DIR/fastembed)
if [ ! -s "$MODELS_DIR/$GGUF_NAME" ]; then
  echo "   fetching $GGUF_NAME ..."
  curl -fL --retry 3 -o "$MODELS_DIR/$GGUF_NAME.part" "$GGUF_URL"
  mv "$MODELS_DIR/$GGUF_NAME.part" "$MODELS_DIR/$GGUF_NAME"
else
  echo "   model present: $GGUF_NAME"
fi

umask 077; env_file="$(mktemp)"; printf '%s' "$ENVB64" | base64 -d > "$env_file"
sudo mkdir -p /opt/c360; sudo mv "$env_file" /opt/c360/docs-vector-search.env; sudo chmod 600 /opt/c360/docs-vector-search.env

if [ "$DEPLOY_MODE" = "ghcr" ]; then
  echo "   pulling $IMAGE ..."
  [ -n "$GHCR_TOKEN" ] && printf %s "$GHCR_TOKEN" | sudo docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
  sudo docker pull "$IMAGE"
  RUN_IMG="$IMAGE"
else
  sed -i 's/ --mount=[^ ]*//g' /opt/c360/tools/docs-vector-search/Dockerfile 2>/dev/null || true
  sudo docker build -t customer360-docs-vector-search-local /opt/c360/tools/docs-vector-search
  RUN_IMG="customer360-docs-vector-search-local"
fi

VOLS=(-v "$CORPUS_DIR:/app/corpus:ro" -v "$MODELS_DIR:/app/models")

# refresh the index first (chunk -> embed -> upsert into pgvector; creates the rag
# schema idempotently). --rm so it never lingers holding RAM alongside the server.
echo "   enrich: building/refreshing the pgvector index ..."
sudo docker run --rm --network host --env-file /opt/c360/docs-vector-search.env "${VOLS[@]}" "$RUN_IMG" python -m src.enrich

# then serve (image default CMD: uvicorn src.server:app --port 8000)
sudo docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
sudo docker run -d --name "$CONTAINER" --restart unless-stopped --network host \
  --env-file /opt/c360/docs-vector-search.env "${VOLS[@]}" "$RUN_IMG"
sleep 5
curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && echo "   health OK (:$PORT/health)" || echo "   WARN: health not ready yet (models load on first request)"
sudo docker ps --filter name="$CONTAINER" --format '   running: {{.Names}} ({{.Status}})'
REMOTE
echo ">> Done. Expose via the LB (add a 'docs' backend -> <box-ip>:$PORT) if it needs public access."

# --- release ledger: record this deploy to the GitHub Deployments API (best-effort) ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/record_deploy.sh"
record_deployment "$ENV" "$SERVICE" "${IMAGE:-local}" success
