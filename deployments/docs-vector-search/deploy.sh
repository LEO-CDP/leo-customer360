#!/usr/bin/env bash
# Deploy the Document Vector Search agent to a vServer via docker compose.
#
#   ./deploy.sh uat        # or: ./deploy.sh prod
#
# 1 CPU / 2 GB RAM (enforced by docker-compose.yml). Fully local models; vectors in
# pgvector on the vDB. Steps: fetch the generator model → build image → build/refresh
# the index (chunk → embed → pgvector) → start a serve-only container.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

ENV="${1:-}"
case "$ENV" in
  uat | prod) ;;
  *) echo "usage: $0 <uat|prod>"; exit 1 ;;
esac

ENV_FILE=".env.$ENV"
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found — copy $ENV_FILE.example to $ENV_FILE and fill it in."
  exit 1
fi
cp "$ENV_FILE" .env

PROJECT="docs-vector-search-$ENV"
export MODELS_DIR_HOST="./models-$ENV"     # host-persisted model weights (per env)
GGUF="$MODELS_DIR_HOST/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"
say() { printf '\033[1;36m▸\033[0m %s\n' "$*"; }

say "[$ENV] Ensure pgvector schema on the vDB"
echo "     Apply sql/schema.sql to the $ENV vDB once (e.g. via ../postgres/run-sql.sh)."
echo "     enrich also creates it idempotently, so this is optional if the DB user may CREATE EXTENSION."

say "[$ENV] Fetch the generator model (if missing)"
mkdir -p "$MODELS_DIR_HOST"
if [ ! -f "$GGUF" ]; then
  curl -fsSL -o "$GGUF" \
    "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"
fi

say "[$ENV] Build image"
docker compose -p "$PROJECT" build

say "[$ENV] Build / refresh the index (chunk -> embed -> pgvector)"
docker compose -p "$PROJECT" run --rm docs-vector-search python -m src.enrich

say "[$ENV] Start the service"
docker compose -p "$PROJECT" up -d

PORT="$(grep -E '^API_PORT=' "$ENV_FILE" | cut -d= -f2)"
say "[$ENV] Done. Health:  curl http://localhost:${PORT}/health"
