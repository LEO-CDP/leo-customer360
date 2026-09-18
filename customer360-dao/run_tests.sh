#!/usr/bin/env bash
set -euo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_HOME}/.venv"

if [ ! -x "${VENV_DIR}/bin/python" ]; then
    python3 -m venv "$VENV_DIR"
fi

VENV_PYTHON="${VENV_DIR}/bin/python"
"$VENV_PYTHON" -m pip install -q --upgrade -e "${PROJECT_HOME}[test]"
exec "$VENV_PYTHON" -m pytest -q "${PROJECT_HOME}/src/tests" "$@"