#!/usr/bin/env bash
###############################################################################
# Customer 360 / Identity Resolution API
# Restarts the API: stop.sh followed by start.sh.
###############################################################################
set -Eeuo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_HOME"

ROOT_ENV_FILE="$PROJECT_HOME/../.env"
LOCAL_ENV_FILE="$PROJECT_HOME/.env"
ENV_FILE=""
LOG_DIR="$PROJECT_HOME/logs"
LOG_FILE="$LOG_DIR/app.log"

GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[1;33m"
NC="\033[0m"

mkdir -p "$LOG_DIR"

# Echo to the console and append a timestamped, color-free copy to LOG_FILE.
log() {
    local msg="$1"
    echo -e "$msg"
    echo "$(date '+%Y-%m-%d %H:%M:%S') $msg" | sed -E 's/\x1b\[[0-9;]*m//g' >>"$LOG_FILE"
}

###############################################################################
# Resolve .env source. Prefer repo-root .env so this script reports the same
# settings that start.sh actually uses.
###############################################################################
if [ -f "$ROOT_ENV_FILE" ]; then
    ENV_FILE="$ROOT_ENV_FILE"
elif [ -f "$LOCAL_ENV_FILE" ]; then
    ENV_FILE="$LOCAL_ENV_FILE"
fi

###############################################################################
# Load .env so SSO_LOGIN (and other settings) can be reported before restart.
###############################################################################
if [ -n "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

###############################################################################
# SSO_LOGIN controls whether the API enforces Keycloak auth on requests.
# This is security-critical, so it must always be visible in the log.
###############################################################################
if [ "${SSO_LOGIN:-false}" = "true" ]; then
    log "${GREEN}SSO_LOGIN=true -- API authentication is ENABLED (Keycloak token required).${NC}"
else
    log "${RED}SSO_LOGIN=false -- API authentication is DISABLED (all requests allowed).${NC}"
fi

log "${GREEN}Restarting Customer 360 API...${NC}"
"$PROJECT_HOME/stop.sh"
"$PROJECT_HOME/start.sh"
