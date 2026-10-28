#!/usr/bin/env bash
# Restart the locally managed Customer 360 event API.
# stop.sh handles graceful shutdown; start.sh recreates the runtime if needed.

set -Eeuo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Keep restart behavior identical to invoking the lifecycle scripts separately.
"$PROJECT_HOME/stop.sh"
exec "$PROJECT_HOME/start.sh"