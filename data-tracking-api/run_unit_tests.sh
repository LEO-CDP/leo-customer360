#!/usr/bin/env bash
###############################################################################
# data-tracking-api -- Unit Test Runner
#
# Runs the pytest suite in tests/ (event-ingestion endpoints, S3 NDJSON sink,
# rate-limit / session-cache fail-open behaviour). No real S3/Redis needed —
# the tests stub the boto3 / redis clients.
#
# Creates/reuses a local .venv, installs requirements (test deps included:
# pytest, httpx), then runs pytest. Mirrors ads-server/run_unit_tests.sh.
#
# Usage:
#   ./run_unit_tests.sh                       # run the whole suite
#   ./run_unit_tests.sh tests/test_tracking.py -k rate_limit   # pass args to pytest
###############################################################################
set -Eeuo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_HOME"

VENV_DIR="$PROJECT_HOME/.venv"

# Create the venv on first run; recreate if it was moved/copied (stale prefix).
if [ ! -x "$VENV_DIR/bin/python" ]; then
  python3 -m venv "$VENV_DIR"
else
  VENV_PREFIX="$("$VENV_DIR/bin/python" -c 'import os,sys; print(os.path.realpath(sys.prefix))' 2>/dev/null || true)"
  [ "$VENV_PREFIX" = "$VENV_DIR" ] || python3 -m venv --clear "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

# Default to the whole tests/ dir; forward any args to pytest.
exec python -m pytest "${@:-tests}" -q
