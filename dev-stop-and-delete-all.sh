#!/bin/bash
set -euo pipefail

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_FILE="dev-docker-compose.yml"
DOCS_COMPOSE_FILE="tools/docs-vector-search/docker-compose.yml"
DOCS_ENV_FILE="tools/docs-vector-search/.env"

# Check if the compose file exists
if [ ! -f "$COMPOSE_FILE" ]; then
    echo "❌ Error: $COMPOSE_FILE not found in $(pwd)"
    exit 1
fi

echo "🧹 Stopping and removing all containers, volumes, and networks defined in $COMPOSE_FILE ..."
docker compose -f "$COMPOSE_FILE" down -v --remove-orphans

if [ -f "$DOCS_ENV_FILE" ]; then
    echo "🧹 Stopping and removing docs-vector-search ..."
    docker compose --profile cpu --profile gpu --env-file "$DOCS_ENV_FILE" -f "$DOCS_COMPOSE_FILE" down -v --remove-orphans
else
    echo "🧹 Removing docs-vector-search container ..."
    docker rm -f docs-vector-search >/dev/null 2>&1 || true
fi

# The network may still exist if other projects use it; we check and optionally remove it.
NETWORK_NAME="customer360-network"
if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    echo "⚠️  Network $NETWORK_NAME still exists. Removing..."
    docker network rm "$NETWORK_NAME" || echo "⚠️  Failed to remove network (possibly in use by other containers)."
else
    echo "✅ Network $NETWORK_NAME already removed."
fi

echo "✅ Cleanup complete."