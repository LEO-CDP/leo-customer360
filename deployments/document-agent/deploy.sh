#!/usr/bin/env bash
# Deploy the AI Chat API (docs-vector-search) to a vServer via docker compose.
#
#   ./deploy.sh uat        # or: ./deploy.sh prod
#
# Sizing: 1 CPU / 2 GB RAM (enforced by docker-compose.yml). Builds the image,
# builds/refreshes the OpenAI vector index into a persisted volume, then (re)starts
# a serve-only container. Safe to re-run — enrich is idempotent, so an unchanged
# corpus re-embeds nothing.
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
cp "$ENV_FILE" .env   # compose reads .env for interpolation AND the container env_file

PROJECT="ai-chat-api-$ENV"
say() { printf '\033[1;36m▸\033[0m %s\n' "$*"; }

say "[$ENV] Building image"
docker compose -p "$PROJECT" build

say "[$ENV] Building / refreshing the vector index (OpenAI)"
docker compose -p "$PROJECT" run --rm ai-chat-api python -m src.enrich

say "[$ENV] Starting the service"
docker compose -p "$PROJECT" up -d

PORT="$(grep -E '^API_PORT=' "$ENV_FILE" | cut -d= -f2)"
say "[$ENV] Done. Health:  curl http://localhost:${PORT}/health"
