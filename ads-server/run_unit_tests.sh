#!/usr/bin/env bash
###############################################################################
# LEO AD SERVER API -- Unit Test Runner
#
# Runs the comprehensive unit test suite in tests/ covering:
# - ORM models (Ad, Campaign, Creative, Placement) and field validation
# - Repository query logic and multi-tenancy filtering
# - API endpoints (health checks, ad delivery, placement lookups)
# - Multi-tenant isolation and error handling
#
# No real PostgreSQL required for model/unit tests (uses in-memory SQLite).
# For integration tests with PostgreSQL, use docker-compose (see README.md).
#
# Usage:
#   ./run_unit_tests.sh                        # run entire test suite
#   ./run_unit_tests.sh tests/test_models.py   # run specific test file
#   ./run_unit_tests.sh -v --tb=short -k ad    # pass args to pytest
###############################################################################
set -Eeuo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_HOME"

GREEN="\033[0;32m"
YELLOW="\033[1;33m"
NC="\033[0m"

VENV_DIR="$PROJECT_HOME/.venv"

###############################################################################
# Virtual environment (create on first run, then reuse)
###############################################################################
RECREATE_VENV=0
if [ ! -d "$VENV_DIR" ] || [ ! -x "$VENV_DIR/bin/python" ]; then
    RECREATE_VENV=1
else
    # A moved/copied venv can keep stale paths; recreate if sys.prefix no longer matches.
    VENV_PREFIX="$($VENV_DIR/bin/python -c 'import os, sys; print(os.path.realpath(sys.prefix))' 2>/dev/null || true)"
    if [ "$VENV_PREFIX" != "$VENV_DIR" ]; then
        echo -e "${YELLOW}Detected stale virtual environment (prefix: ${VENV_PREFIX}). Recreating...${NC}"
        RECREATE_VENV=1
    fi
fi

if [ "$RECREATE_VENV" -eq 1 ]; then
    rm -rf "$VENV_DIR"
    echo -e "${GREEN}Creating virtual environment at ${VENV_DIR}...${NC}"
    python3 -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"

echo -e "${GREEN}Installing requirements...${NC}"
"$VENV_PYTHON" -m pip install -q -r requirements.txt

###############################################################################
# Load .env so LEO_AD_DB_* variables are available for the PostgreSQL check below
###############################################################################
if [ -f "$PROJECT_HOME/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_HOME/.env"
    set +a
fi

###############################################################################
# Check PostgreSQL availability for integration tests
###############################################################################
echo -e "${YELLOW}Checking for PostgreSQL...${NC}"

POSTGRES_AVAILABLE=0
TEST_FILES=""
if command -v psql &> /dev/null; then
    if PGPASSWORD="${LEO_AD_DB_PASSWORD:-}" psql -h "${LEO_AD_DB_HOST:-localhost}" -p "${LEO_AD_DB_PORT:-5432}" -U "${LEO_AD_DB_USER:-postgres}" -d "${LEO_AD_DB_NAME:-customer360}" -tAc "SELECT to_regclass('leo_ads.tenant') IS NOT NULL" -w 2>/dev/null | grep -qx "t"; then
        POSTGRES_AVAILABLE=1
        echo -e "${GREEN}✓ PostgreSQL and leo_ads schema available - running full test suite${NC}"
    else
        echo -e "${YELLOW}⚠ PostgreSQL or leo_ads schema unavailable - running model tests only${NC}"
        echo -e "${YELLOW}  (Repository/API tests require PostgreSQL with the leo_ads schema)${NC}"
        echo -e "${YELLOW}  To initialize it: psql ... -f sql-scripts/db-schema-init.sql${NC}"
        TEST_FILES="tests/test_models.py tests/test_model_metadata.py"
    fi
else
    echo -e "${YELLOW}⚠ psql not found - running model tests only${NC}"
    echo -e "${YELLOW}  (Repository/API tests require PostgreSQL with the leo_ads schema)${NC}"
    echo -e "${YELLOW}  To initialize it: psql ... -f sql-scripts/db-schema-init.sql${NC}"
    TEST_FILES="tests/test_models.py tests/test_model_metadata.py"
fi

###############################################################################
# Run tests
###############################################################################
echo -e "${YELLOW}Running LEO AD SERVER API unit tests...${NC}"

if [ -z "$TEST_FILES" ]; then
    # PostgreSQL available - run all tests
    "$VENV_PYTHON" -m pytest tests/ -v "$@"
else
    # Only model tests (SQLite-compatible)
    "$VENV_PYTHON" -m pytest $TEST_FILES -v "$@"
fi
