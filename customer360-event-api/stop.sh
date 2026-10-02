#!/usr/bin/env bash
# Stop the locally managed Customer 360 event API process.
# Only the process recorded by start.sh can be terminated.

set -Eeuo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$PROJECT_HOME/.venv/bin/python"
PID_FILE="$PROJECT_HOME/.uvicorn.pid"

LOG_DIR="$PROJECT_HOME/logs"
LOG_FILE="$LOG_DIR/app.log"
mkdir -p "$LOG_DIR"

log() {
    local message="$1"
    printf '%s\n' "$message"
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$message" >> "$LOG_FILE"
}

is_event_api_process() {
    local process_id="$1"
    local process_args
    process_args="$(ps -p "$process_id" -o args= 2>/dev/null || true)"
    [[ "$process_args" == *"$VENV_PYTHON -m uvicorn app:app"* ]]
}

# A missing PID file means there is no process managed by these scripts.
if [[ ! -f "$PID_FILE" ]]; then
    log '[EVENT API] No PID file; service is not running.'
    exit 0
fi

PID="$(<"$PID_FILE")"
# Never trust a stale PID file without checking the command line.
if [[ ! "$PID" =~ ^[0-9]+$ ]] || ! kill -0 "$PID" 2>/dev/null || ! is_event_api_process "$PID"; then
    log '[EVENT API] No matching service process; removing stale PID file.'
    rm -f "$PID_FILE"
    exit 0
fi

log "[EVENT API] Stopping | PID $PID"
kill -TERM "$PID" 2>/dev/null || true

# Allow Uvicorn and the application lifespan to shut down cleanly.
for _ in 1 2 3 4 5; do
    if ! kill -0 "$PID" 2>/dev/null; then
        break
    fi
    sleep 1
done

if kill -0 "$PID" 2>/dev/null && is_event_api_process "$PID"; then
    log '[EVENT API] Graceful shutdown timed out; sending SIGKILL.'
    kill -KILL "$PID" 2>/dev/null || true
fi

rm -f "$PID_FILE"
if kill -0 "$PID" 2>/dev/null && is_event_api_process "$PID"; then
    log "[EVENT API] Failed to stop | PID $PID is still running" >&2
    exit 1
fi

log "[EVENT API] Stopped | PID $PID"