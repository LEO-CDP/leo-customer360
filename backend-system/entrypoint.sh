#!/usr/bin/env bash
# Render the Dagster instance config, then launch Dagster.
#
# render_dagster_instance.py probes Postgres + S3 and writes an ADAPTIVE
# $DAGSTER_HOME/dagster.yaml: shared PostgreSQL + S3 compute logs when reachable,
# else local SQLite + local compute logs. It never fails hard, so the
# orchestrator ALWAYS starts — on local, UAT and PROD, with or without those
# backends. Then we exec the command passed as arguments (e.g. `dagster dev`).
set -uo pipefail

python /app/scripts/render_dagster_instance.py \
  || echo "[entrypoint] instance render failed; Dagster will use local defaults"

exec "$@"
