#!/usr/bin/env bash
###############################################################################
# LEO AD SERVER / Identity Resolution API
#
# Starts the FastAPI app (app.py) with uvicorn in the background.
###############################################################################

set -Eeuo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_HOME"

VENV_DIR="$PROJECT_HOME/.venv"
ENV_FILE="$PROJECT_HOME/.env"
LOG_DIR="$PROJECT_HOME/logs"
PID_FILE="$PROJECT_HOME/.uvicorn.pid"
LOG_FILE="$LOG_DIR/app.log"

GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[1;33m"
NC="\033[0m"

mkdir -p "$LOG_DIR"

###############################################################################
# Logging
###############################################################################

# Echo to console and append a timestamped, color-free copy to LOG_FILE.
log() {
    local msg="$1"

    echo -e "$msg"
    echo "$(date '+%Y-%m-%d %H:%M:%S') $msg" \
        | sed -E 's/\x1b\[[0-9;]*m//g' \
        >> "$LOG_FILE"
}

###############################################################################
# Refuse to start twice
###############################################################################

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    log "${YELLOW}[API] Already running${NC} | PID $(cat "$PID_FILE")"
    log "${YELLOW}[API] Stop first${NC} | ./stop.sh"
    exit 0
fi

###############################################################################
# Virtual environment
###############################################################################

RECREATE_VENV=0

if [ ! -d "$VENV_DIR" ] || [ ! -x "$VENV_DIR/bin/python" ]; then
    RECREATE_VENV=1
else
    # A moved/copied venv can keep stale paths; recreate if sys.prefix
    # no longer matches the expected virtual environment directory.
    VENV_PREFIX="$(
        "$VENV_DIR/bin/python" \
            -c 'import os, sys; print(os.path.realpath(sys.prefix))' \
            2>/dev/null || true
    )"

    if [ "$VENV_PREFIX" != "$VENV_DIR" ]; then
        log "${YELLOW}[VENV] Stale environment detected${NC} | prefix: ${VENV_PREFIX}"
        log "${YELLOW}[VENV] Recreating virtual environment...${NC}"
        RECREATE_VENV=1
    fi
fi

if [ "$RECREATE_VENV" -eq 1 ]; then
    rm -rf "$VENV_DIR"

    log "${GREEN}[VENV] Creating environment${NC} | ${VENV_DIR}"
    python3 -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"

###############################################################################
# Install requirements
###############################################################################

log "[DEPS] Installing requirements..."
"$VENV_PYTHON" -m pip install -q -r requirements.txt
log "${GREEN}[DEPS] Requirements ready${NC}"

###############################################################################
# Ensure .env exists
#
# Dev mode:
#   If service-local .env is missing, create a symlink to ../.env.
#
# Docker:
#   Skip this because environment variables should be injected by Docker.
###############################################################################

if [ ! -f "$ENV_FILE" ] && [ ! -L "$ENV_FILE" ] && [ ! -f /.dockerenv ]; then
    if [ -f "$PROJECT_HOME/../.env" ]; then
        log "${YELLOW}[ENV] ${ENV_FILE} not found${NC}"
        log "${YELLOW}[ENV] Linking to ../.env${NC}"

        ln -s ../.env "$ENV_FILE"
    fi
fi

###############################################################################
# Load .env
###############################################################################

if [ -f "$ENV_FILE" ]; then
    log "${GREEN}[ENV] Loading environment${NC} | ${ENV_FILE}"

    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
else
    log "${YELLOW}[ENV] ${ENV_FILE} not found${NC} | Using environment defaults"
fi

###############################################################################
# Runtime configuration
###############################################################################

HOST="${C360_API_HOST:-0.0.0.0}"
PORT="${C360_API_PORT:-8008}"

###############################################################################
# SSO / Authentication
###############################################################################

# SSO_LOGIN controls whether the API enforces Keycloak authentication.
# This is security-critical and must always be visible in the startup log.

if [ "${SSO_LOGIN:-false}" = "true" ]; then
    log "${GREEN}[AUTH] SSO_LOGIN: ENABLED${NC}  | Keycloak authentication required"
else
    log "${RED}[AUTH] SSO_LOGIN: DISABLED${NC} | API authentication bypassed"
fi

###############################################################################
# Uvicorn configuration
###############################################################################

RELOAD_FLAG=""

if [ "${UVICORN_RELOAD:-false}" = "true" ]; then
    RELOAD_FLAG="--reload"
    log "${YELLOW}[UVICORN] Auto-reload: ENABLED${NC}"
else
    log "[UVICORN] Auto-reload: disabled"
fi

###############################################################################
# Start uvicorn
###############################################################################

log "${GREEN}[API] Starting LEO AD SERVER API${NC}"
log "[API] Endpoint | http://${HOST}:${PORT}"

nohup "$VENV_PYTHON" \
    -m uvicorn app:app \
    --host "$HOST" \
    --port "$PORT" \
    $RELOAD_FLAG \
    >> "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"

sleep 2

###############################################################################
# Verify startup
###############################################################################

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    PID="$(cat "$PID_FILE")"

    log "${GREEN}[API] Started successfully${NC} | PID ${PID}"
    log "[API] Logs   | ${LOG_FILE}"
    log "[API] Health | curl http://${HOST}:${PORT}/health"
    log "[API] Docs   | http://${HOST}:${PORT}/docs"
else
    log "${RED}[API] Failed to start${NC} | Check ${LOG_FILE}"

    rm -f "$PID_FILE"
    exit 1
fi