#!/usr/bin/env bash
###############################################################################
# Customer 360 / Identity Resolution API
#
# Restarts the API: stop.sh followed by start.sh.
###############################################################################

set -Eeuo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_HOME"

ENV_FILE="$PROJECT_HOME/.env"
LOG_DIR="$PROJECT_HOME/logs"
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
# Load environment
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
# Authentication status
###############################################################################

# SSO_LOGIN controls whether the API enforces Keycloak authentication.
# This is security-critical and should always be visible during restart.

if [ "${SSO_LOGIN:-false}" = "true" ]; then
    log "${GREEN}[AUTH] SSO_LOGIN: ENABLED${NC}  | Keycloak authentication required"
else
    log "${RED}[AUTH] SSO_LOGIN: DISABLED${NC} | API authentication bypassed"
fi

###############################################################################
# Restart API
###############################################################################

log "${GREEN}[API] Restarting Customer 360 API...${NC}"

"$PROJECT_HOME/stop.sh"
"$PROJECT_HOME/start.sh"

###############################################################################
# Restart complete
###############################################################################

log "${GREEN}[API] Restart completed successfully${NC}"