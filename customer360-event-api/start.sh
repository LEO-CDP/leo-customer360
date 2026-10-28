#!/usr/bin/env bash
# Start the Customer 360 event API locally without Docker.
# The service runs in the project virtualenv and is managed through a PID file.

set -Eeuo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_HOME"

VENV_DIR="$PROJECT_HOME/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
ENV_FILE="$PROJECT_HOME/.env"
LOG_DIR="$PROJECT_HOME/logs"
LOG_FILE="$LOG_DIR/app.log"
PID_FILE="$PROJECT_HOME/.uvicorn.pid"

mkdir -p "$LOG_DIR"

# Write lifecycle messages to both the terminal and the service log.
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

# Refuse to start a second event API and remove a stale PID file when needed.
if [[ -f "$PID_FILE" ]]; then
    PID="$(<"$PID_FILE")"
    if [[ "$PID" =~ ^[0-9]+$ ]] && kill -0 "$PID" 2>/dev/null && is_event_api_process "$PID"; then
        log "[EVENT API] Already running | PID $PID"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# Recreate an existing environment if it is missing or below the Python floor.
if [[ ! -x "$VENV_PYTHON" ]] || ! "$VENV_PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' 2>/dev/null; then
    if command -v python3.12 >/dev/null 2>&1; then
        BASE_PYTHON="$(command -v python3.12)"
    elif command -v python3 >/dev/null 2>&1; then
        BASE_PYTHON="$(command -v python3)"
    else
        log "[EVENT API] Python 3.12+ is required; install it before starting the service."
        exit 1
    fi

    if ! "$BASE_PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
        log "[EVENT API] Python 3.12+ is required; found $($BASE_PYTHON --version)."
        exit 1
    fi

    if [[ -d "$VENV_DIR" ]]; then
        log "[VENV] Recreating environment with Python 3.12+ | $VENV_DIR"
        rm -rf "$VENV_DIR"
    else
        log "[VENV] Creating environment | $VENV_DIR"
    fi
    "$BASE_PYTHON" -m venv "$VENV_DIR"
fi

# Reuse the repository-level environment file for local development when present.
if [[ ! -f "$ENV_FILE" && ! -L "$ENV_FILE" && -f "$PROJECT_HOME/../.env" ]]; then
    ln -s ../.env "$ENV_FILE"
    log "[ENV] Linked service .env to ../.env"
fi

if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

# Keep dependencies in the local environment synchronized before launch.
log "[DEPS] Installing requirements"
"$VENV_PYTHON" -m pip install -q -r requirements.txt

HOST="${C360_TRACKING_API_HOST:-127.0.0.1}"
PORT="${C360_TRACKING_API_PORT:-8010}"
RELOAD_ARGS=()
# Opt into Uvicorn reload explicitly; production-like local runs stay single-process.
if [[ "${C360_TRACKING_UVICORN_RELOAD:-false}" == "true" ]]; then
    RELOAD_ARGS=(--reload)
fi

log "[EVENT API] Starting at http://${HOST}:${PORT}"
nohup "$VENV_PYTHON" -m uvicorn app:app \
    --host "$HOST" \
    --port "$PORT" \
    "${RELOAD_ARGS[@]}" \
    >> "$LOG_FILE" 2>&1 &

PID=$!
printf '%s\n' "$PID" > "$PID_FILE"
sleep 2

# Verify that the expected process is alive before reporting success.
if kill -0 "$PID" 2>/dev/null && is_event_api_process "$PID"; then
    log "[EVENT API] Started | PID $PID"
    log "[EVENT API] Logs: $LOG_FILE"
    log "[EVENT API] Health: http://${HOST}:${PORT}/health"
else
    log "[EVENT API] Failed to start; check $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi