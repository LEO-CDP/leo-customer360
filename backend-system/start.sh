#!/usr/bin/env bash
###############################################################################
# Customer 360 / backend-system - Dagster local dev orchestrator
#
# Starts `dagster dev` (webserver UI on DAGSTER_UI_PORT + the Dagster daemon)
# loading every backend-system service via workspace.yaml
# (identity_resolution, scoring, segmentation, analytics) -- LOCAL DEV ONLY.
# Not used by docker-compose.yml (each service still ships its own container
# there -- see identity_resolution/Dockerfile). See README.md for the full
# architecture writeup.
###############################################################################
set -Eeuo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_HOME"

VENV_DIR="$PROJECT_HOME/.venv"
ROOT_ENV_FILE="$PROJECT_HOME/../.env"
LOCAL_ENV_FILE="$PROJECT_HOME/.env"
ENV_FILE=""
LOG_DIR="$PROJECT_HOME/logs"
PID_FILE="$PROJECT_HOME/.dagster.pid"
LOG_FILE="$LOG_DIR/dagster.log"

# Every backend-system code location registered in workspace.yaml. Keep in
# sync with that file -- see README.md "add a new backend service" section.
SERVICES=(identity_resolution scoring segmentation analytics)

GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[1;33m"
NC="\033[0m"

mkdir -p "$LOG_DIR"

# Echo to the console and append a timestamped, color-free copy to LOG_FILE.
log() {
    local msg="$1"
    echo -e "$msg"
    echo -e "$(date '+%Y-%m-%d %H:%M:%S') $msg" | sed -E 's/\x1b\[[0-9;]*m//g' >>"$LOG_FILE"
}

###############################################################################
# Refuse to start twice
###############################################################################
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    log "${YELLOW}Already running (PID $(cat "$PID_FILE")). Use ./stop.sh first.${NC}"
    exit 0
fi

###############################################################################
# Virtual environment (create on first run, then reuse). Shared across all
# services for local-dev simplicity -- see workspace.yaml's note about
# giving each code location its own venv (`executable_path`) once their
# dependencies diverge enough to matter.
###############################################################################
RECREATE_VENV=0
if [ ! -d "$VENV_DIR" ] || [ ! -x "$VENV_DIR/bin/python" ]; then
    RECREATE_VENV=1
else
    # A moved/copied venv can keep stale paths; recreate if sys.prefix no longer matches.
    VENV_PREFIX="$($VENV_DIR/bin/python -c 'import os, sys; print(os.path.realpath(sys.prefix))' 2>/dev/null || true)"
    if [ "$VENV_PREFIX" != "$VENV_DIR" ]; then
        log "${YELLOW}Detected stale virtual environment (prefix: ${VENV_PREFIX}). Recreating...${NC}"
        RECREATE_VENV=1
    fi
fi

if [ "$RECREATE_VENV" -eq 1 ]; then
    rm -rf "$VENV_DIR"
    log "${GREEN}Creating virtual environment at ${VENV_DIR}...${NC}"
    python3 -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"

log "Installing requirements for: ${SERVICES[*]} (+ requirements-dev.txt for the local Dagster UI)..."
for svc in "${SERVICES[@]}"; do
    "$VENV_PYTHON" -m pip install -q -r "${PROJECT_HOME}/${svc}/requirements.txt"
done
"$VENV_PYTHON" -m pip install -q -r "${PROJECT_HOME}/requirements-dev.txt"

###############################################################################
# Resolve .env source. Prefer repo-root .env so this uses the same
# CIR_*/DB_*/etc settings as the rest of the stack (dev-start-all.sh,
# docker compose). Fall back to a backend-system-local .env.
###############################################################################
if [ -f "$ROOT_ENV_FILE" ]; then
    ENV_FILE="$ROOT_ENV_FILE"
elif [ -f "$LOCAL_ENV_FILE" ]; then
    ENV_FILE="$LOCAL_ENV_FILE"
fi

if [ -n "$ENV_FILE" ]; then
    log "${GREEN}Loading ${ENV_FILE}...${NC}"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
else
    log "${YELLOW}Warning: no .env file found at ${ROOT_ENV_FILE} or ${LOCAL_ENV_FILE}. Using default environment variables.${NC}"
fi

###############################################################################
# DAGSTER_HOME must be an ABSOLUTE path (Dagster requirement). Default to a
# persistent, gitignored directory under this service so run history
# survives across ./stop.sh + ./start.sh cycles -- without it, Dagster falls
# back to a throwaway ephemeral instance wiped on every restart.
###############################################################################
export DAGSTER_HOME="${DAGSTER_HOME:-$PROJECT_HOME/.dagster_home}"
mkdir -p "$DAGSTER_HOME"

HOST="${DAGSTER_UI_HOST:-127.0.0.1}"
PORT="${DAGSTER_UI_PORT:-3000}"

###############################################################################
# Start `dagster dev` (webserver + daemon) in the background.
#
# `setsid` makes the dagster process a new session/process-group leader
# (detached from this script's terminal) so stop.sh can reliably terminate
# the *entire* group (webserver + daemon + per-service code servers) with a
# single `kill -- -PID`, instead of only the top-level process (which, left
# running, does NOT always cascade the signal to every child in time).
###############################################################################
log "DAGSTER_HOME=${DAGSTER_HOME}"
log "Starting Dagster (webserver + daemon) on http://${HOST}:${PORT} ..."
setsid "$VENV_DIR/bin/dagster" dev -w workspace.yaml -h "$HOST" -p "$PORT" >>"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"

sleep 3

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    log "${GREEN}Started (PID $(cat "$PID_FILE")). Logs: ${LOG_FILE}${NC}"
    echo "Dagster UI:      http://${HOST}:${PORT}"
    echo "Code locations:  ${SERVICES[*]}"
    echo "Tail logs:       tail -f ${LOG_FILE}"
else
    log "${RED}Failed to start -- check ${LOG_FILE}${NC}"
    rm -f "$PID_FILE"
    exit 1
fi
