#!/usr/bin/env bash
###############################################################################
# customer360-agent — unit test runner.
#
# Hermetic: no live LLM / Postgres. LiteLLM is mocked; the prompt store is
# exercised via injected snapshots (see tests/). conftest.py puts src/ on the
# path. Installs deps into a local .venv on first run, then reuses it.
#
# Usage:
#   ./run_unit_tests.sh                 # whole suite
#   ./run_unit_tests.sh -k provider     # extra args pass straight to pytest
###############################################################################
set -Eeuo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_HOME"

VENV_DIR="$PROJECT_HOME/.venv"
PY="${PYTHON:-python3}"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PY" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

# -p no:cacheprovider: CI checkouts are read-only-ish; skip the .pytest_cache write.
exec python -m pytest -q -p no:cacheprovider "$@"
