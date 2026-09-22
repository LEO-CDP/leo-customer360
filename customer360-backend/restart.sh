#!/usr/bin/env bash
###############################################################################
# Customer 360 / customer360-backend - Dagster local dev orchestrator
# Restarts Dagster: stop.sh followed by start.sh.
###############################################################################
set -Eeuo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_HOME"

echo "Restarting customer360-backend Dagster..."
"$PROJECT_HOME/stop.sh"
"$PROJECT_HOME/start.sh"
