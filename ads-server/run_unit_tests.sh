#!/usr/bin/env bash
###############################################################################
# LEO AD SERVER  API -- unit test runner.
#
# Runs the hermetic unit test suite in tests/ (auth middleware, Keycloak
# login provisioning, multi-tenant Row-Level Security GUC wiring, generic
# tenant-scoped CRUD router). No real PostgreSQL/Redis/Keycloak is required
# -- every external dependency is faked/mocked (see tests/conftest.py).
#
# Usage:
#   ./run_unit_tests.sh                # run the whole suite
#   ./run_unit_tests.sh -k keycloak     # pass extra args straight to pytest
#   ./run_unit_tests.sh tests/test_auth_middleware.py
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

echo -e "${YELLOW}Running LEO AD SERVER API unit tests...${NC}"
"$VENV_PYTHON" -m pytest tests/ -v "$@"
