#!/usr/bin/env bash
###############################################################################
# Customer 360 / Identity Resolution API
#
# Stops the uvicorn process started by start.sh.
###############################################################################

set -Eeuo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$PROJECT_HOME/.uvicorn.pid"

GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

###############################################################################
# Resolve PID
###############################################################################

PID=""

# Prefer the PID file created by start.sh.
if [ -f "$PID_FILE" ]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
fi

# Fallback: find uvicorn process if the PID file is missing or stale.
if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    PID="$(pgrep -f "uvicorn app:app" | head -n 1 || true)"
fi

###############################################################################
# Nothing to stop
###############################################################################

if [ -z "$PID" ]; then
    echo -e "${YELLOW}[API] No running Customer 360 API process found.${NC}"
    rm -f "$PID_FILE"
    exit 0
fi

###############################################################################
# Graceful shutdown
###############################################################################

echo "[API] Stopping Customer 360 API | PID ${PID}"

kill "$PID" 2>/dev/null || true

for _ in 1 2 3 4 5; do
    if ! kill -0 "$PID" 2>/dev/null; then
        break
    fi

    sleep 1
done

###############################################################################
# Force shutdown if necessary
###############################################################################

if kill -0 "$PID" 2>/dev/null; then
    echo -e "${YELLOW}[API] Graceful shutdown timed out${NC} | Sending SIGKILL"

    kill -9 "$PID" 2>/dev/null || true
fi

###############################################################################
# Cleanup
###############################################################################

rm -f "$PID_FILE"

if kill -0 "$PID" 2>/dev/null; then
    echo -e "${RED}[API] Failed to stop${NC} | PID ${PID} is still running"
    exit 1
fi

echo -e "${GREEN}[API] Stopped successfully${NC} | PID ${PID}"