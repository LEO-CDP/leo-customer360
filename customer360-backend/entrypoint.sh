#!/usr/bin/env bash
# Render the production Dagster instance config, then launch Dagster.
set -Eeuo pipefail

case "${1:-}" in
  dagster|dagster-webserver|dagster-daemon)
    python /app/scripts/render_dagster_instance.py
    ;;
esac

exec "$@"
