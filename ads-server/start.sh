#!/usr/bin/env bash
###############################################################################
# LEO AD SERVER
#
# Starts the FastAPI app (app.py) with uvicorn in the background.
#
# Usage:
#   ./start.sh
#       Start the FastAPI app with uvicorn.
#
#   ./start.sh --seed-demo-ads-server
#       Ensure database schema exists, seed demo data if leo_ads.tenant
#       is empty, then start the API.
#
# Environment variables (from .env):
#   DB_HOST                 PostgreSQL host (default: localhost)
#   DB_PORT                 PostgreSQL port (default: 5432)
#   LEO_AD_API_HOST         AD SERVER API listen address (default: localhost)
#   LEO_AD_API_PORT         AD SERVER API listen port (default: 9009)
#   UVICORN_RELOAD          Enable auto-reload (default: false)
###############################################################################

set -Eeuo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_HOME"

VENV_DIR="$PROJECT_HOME/.venv"
ENV_FILE="$PROJECT_HOME/.env"
LOG_DIR="$PROJECT_HOME/logs"
PID_FILE="$PROJECT_HOME/.uvicorn.pid"
LOG_FILE="$LOG_DIR/app.log"
SQL_DIR="$PROJECT_HOME/sql-scripts"

GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[1;33m"
NC="\033[0m"

###############################################################################
# Command-line arguments
###############################################################################

SEED_DEMO=false

for arg in "$@"; do
    case "$arg" in
        --seed-demo-ads-server)
            SEED_DEMO=true
            ;;
        --help|-h)
            head -n 18 "$0" | tail -n +2 | sed 's/^# //'
            exit 0
            ;;
        *)
            echo "❌ Unknown argument: $arg (use --help for usage)" >&2
            exit 1
            ;;
    esac
done

mkdir -p "$LOG_DIR"

###############################################################################
# Logging
###############################################################################

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

HOST="${LEO_AD_API_HOST:-localhost}"
PORT="${LEO_AD_API_PORT:-9009}"

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-change_me_postgres_password}"
DB_NAME="${DB_NAME:-customer360}"
DB_CONTAINER_NAME="${DB_CONTAINER_NAME:-customer360-postgres}"

###############################################################################
# PostgreSQL helpers
###############################################################################

check_db_available() {
    "$VENV_PYTHON" - "$DB_HOST" "$DB_PORT" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(2)

try:
    sock.connect((host, port))
    print(f"DB_OK {host}:{port}")
    raise SystemExit(0)
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

###############################################################################
# Check whether leo_ads schema exists
###############################################################################

run_sql_query() {
    local sql="$1"

    if command -v psql >/dev/null 2>&1; then
        PGPASSWORD="$DB_PASSWORD" \
            psql \
            -h "$DB_HOST" \
            -p "$DB_PORT" \
            -U "$DB_USER" \
            -d "$DB_NAME" \
            -tAc "$sql" 2>/dev/null
        return
    fi

    if command -v docker >/dev/null 2>&1; then
        docker exec -i -u postgres "$DB_CONTAINER_NAME" \
            env PGPASSWORD="$DB_PASSWORD" \
            psql \
            -h 127.0.0.1 \
            -p 5432 \
            -U "$DB_USER" \
            -d "$DB_NAME" \
            -tAc "$sql" 2>/dev/null
        return
    fi

    return 127
}

run_sql_file() {
    local sql_file="$1"

    if command -v psql >/dev/null 2>&1; then
        PGPASSWORD="$DB_PASSWORD" \
            psql \
            -v ON_ERROR_STOP=1 \
            -h "$DB_HOST" \
            -p "$DB_PORT" \
            -U "$DB_USER" \
            -d "$DB_NAME" \
            -f "$sql_file" \
            >> "$LOG_FILE" 2>&1
        return
    fi

    if ! command -v docker >/dev/null 2>&1; then
        return 127
    fi

    local remote_name
    remote_name="$(basename "$sql_file")"

    if ! docker cp "$sql_file" "$DB_CONTAINER_NAME:/tmp/$remote_name" >/dev/null 2>&1; then
        return 1
    fi

    if ! docker exec -i -u postgres "$DB_CONTAINER_NAME" \
        env PGPASSWORD="$DB_PASSWORD" \
        psql \
        -h 127.0.0.1 \
        -p 5432 \
        -U "$DB_USER" \
        -d "$DB_NAME" \
        -v ON_ERROR_STOP=1 \
        -f "/tmp/$remote_name" \
        >> "$LOG_FILE" 2>&1; then
        docker exec -u postgres "$DB_CONTAINER_NAME" rm -f "/tmp/$remote_name" >/dev/null 2>&1 || true
        return 1
    fi

    docker exec -u postgres "$DB_CONTAINER_NAME" rm -f "/tmp/$remote_name" >/dev/null 2>&1 || true
}

schema_exists() {
    local query
    query="SELECT EXISTS ( SELECT 1 FROM information_schema.schemata WHERE schema_name = 'leo_ads' );"

    run_sql_query "$query" 2>/dev/null | grep -qx "t"
}

###############################################################################
# Check whether leo_ads.tenant exists
###############################################################################

tenant_table_exists() {
    local query
    query="SELECT EXISTS ( SELECT 1 FROM information_schema.tables WHERE table_schema = 'leo_ads' AND table_name = 'tenant' );"

    run_sql_query "$query" 2>/dev/null | grep -qx "t"
}

###############################################################################
# Check whether leo_ads.tenant contains data
#
# Returns:
#   0 -> tenant table exists and contains at least one row
#   1 -> tenant table is empty
#   2 -> tenant table does not exist
###############################################################################

tenant_has_data() {
    local tenant_count

    if ! tenant_table_exists; then
        return 2
    fi

    tenant_count="$(
        run_sql_query "SELECT COUNT(*) FROM leo_ads.tenant;" 2>/dev/null \
            | tr -d '[:space:]'
    )"

    if [ -z "$tenant_count" ]; then
        return 2
    fi

    if [ "$tenant_count" -gt 0 ]; then
        return 0
    fi

    return 1
}

###############################################################################
# Initialize database schema
###############################################################################

init_database_schema() {
    local schema_sql="$SQL_DIR/db-schema-init.sql"

    if [ ! -f "$schema_sql" ]; then
        log "${RED}[SEED] Schema init script not found${NC} | ${schema_sql}"
        return 1
    fi

    log "${YELLOW}[SEED] leo_ads schema not found${NC}"
    log "${YELLOW}[SEED] Initializing database schema${NC}"
    log "[SEED] SQL | ${schema_sql}"

    if ! run_sql_file "$schema_sql"; then
        log "${RED}[SEED] Failed to initialize database schema${NC}"
        log "${RED}[SEED] Check log${NC} | ${LOG_FILE}"
        return 1
    fi

    log "${GREEN}[SEED] Database schema initialized successfully${NC}"

    return 0
}

###############################################################################
# Seed demo data
###############################################################################

seed_sample_data() {
    local sample_sql="$SQL_DIR/sample-data-init.sql"

    if [ ! -f "$sample_sql" ]; then
        log "${RED}[SEED] Sample data script not found${NC} | ${sample_sql}"
        return 1
    fi

    log "${YELLOW}[SEED] leo_ads.tenant is empty${NC}"
    log "${YELLOW}[SEED] Seeding demo ads data${NC}"
    log "[SEED] SQL | ${sample_sql}"

    if ! run_sql_file "$sample_sql"; then
        log "${RED}[SEED] Failed to seed demo data${NC}"
        log "${RED}[SEED] Check log${NC} | ${LOG_FILE}"
        return 1
    fi

    log "${GREEN}[SEED] Demo ads data seeded successfully${NC}"

    return 0
}

###############################################################################
# Seed database
#
# Desired behavior:
#
#   Schema missing
#       |
#       +--> run db-schema-init.sql
#       |
#       v
#   Check leo_ads.tenant
#       |
#       +--> empty --> run sample-data-init.sql
#       |
#       +--> has data --> skip sample seed
#
###############################################################################

seed_database() {
    if [ ! -d "$SQL_DIR" ]; then
        log "${RED}[SEED] SQL scripts directory not found${NC} | ${SQL_DIR}"
        return 1
    fi

    ###########################################################################
    # Step 1: Initialize schema only when it does not exist
    ###########################################################################

    if ! schema_exists; then
        if ! init_database_schema; then
            return 1
        fi
    else
        log "${GREEN}[SEED] leo_ads schema already exists${NC} | skipping schema initialization"
    fi

    ###########################################################################
    # Step 2: Check tenant table
    ###########################################################################

    local tenant_status=0

    tenant_has_data || tenant_status=$?

    case "$tenant_status" in

        0)
            # tenant table has at least one row
            log "${GREEN}[SEED] leo_ads.tenant already contains data${NC}"
            log "${GREEN}[SEED] Skipping demo data seed${NC}"
            ;;

        1)
            # tenant table exists but is empty
            if ! seed_sample_data; then
                return 1
            fi
            ;;

        2)
            # This should normally never happen if the schema script is valid.
            log "${RED}[SEED] leo_ads.tenant table does not exist${NC}"
            log "${RED}[SEED] Database schema may be incomplete${NC}"
            return 1
            ;;

        *)
            log "${RED}[SEED] Unable to determine leo_ads.tenant state${NC}"
            return 1
            ;;
    esac

    return 0
}

###############################################################################
# Ensure local development infrastructure
###############################################################################

ensure_dev_infra() {
    local root_dir
    root_dir="$(cd "$PROJECT_HOME/.." && pwd)"

    local compose_file="$root_dir/dev-docker-compose.yml"

    if [ ! -f "$compose_file" ]; then
        return 1
    fi

    if command -v docker >/dev/null 2>&1; then
        log "${YELLOW}[DB] Starting local infrastructure${NC} | docker compose -f $compose_file up -d postgres redis keycloak"

        (
            cd "$root_dir"

            if docker compose \
                -f "$compose_file" \
                up -d postgres redis keycloak >/dev/null 2>&1; then
                return 0
            fi

            return 1
        )
    fi

    return 1
}

###############################################################################
# Check PostgreSQL
###############################################################################

if ! check_db_available; then
    log "${YELLOW}[DB] PostgreSQL not reachable${NC} | ${DB_HOST}:${DB_PORT}"

    if ensure_dev_infra; then

        if ! check_db_available; then
            log "${RED}[DB] PostgreSQL did not become ready${NC} | ${DB_HOST}:${DB_PORT}"
            exit 1
        fi

    else
        log "${YELLOW}[DB] Start the dev stack first${NC} | cd .. && ./dev-c360.sh"
        log "${YELLOW}[DB] Or boot postgres manually${NC} | docker compose -f ../dev-docker-compose.yml up -d postgres redis keycloak"
        exit 1
    fi
fi

log "${GREEN}[DB] PostgreSQL ready${NC} | ${DB_HOST}:${DB_PORT}/${DB_NAME}"

###############################################################################
# Database initialization / demo seeding
###############################################################################

if [ "$SEED_DEMO" = true ]; then

    log "${YELLOW}[SEED] Demo ads server initialization requested${NC}"

    if ! seed_database; then
        log "${RED}[SEED] Database initialization failed${NC}"
        exit 1
    fi

    log "${GREEN}[SEED] Database initialization completed${NC}"

else
    log "[SEED] Demo database initialization disabled"
    log "[SEED] Use ./start.sh --seed-demo-ads-server to initialize/seed demo data"
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