#!/bin/bash
# Runs the Event Loader Python test suite.
#
# Hermetic unit tests: the Redis consumer logic runs against an in-test FakeRedis
# and the Dagster job/sensor wiring runs in-process (Dagster's own executor) with
# run_loader_once mocked out -- so no real Redis or PostgreSQL is required. A
# repo-root / local .env is loaded when present, matching the sibling runners.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ROOT_ENV_FILE="${SCRIPT_DIR}/../../.env"
LOCAL_ENV_FILE="${SCRIPT_DIR}/.env"
ENV_FILE=""
if [ -f "$ROOT_ENV_FILE" ]; then
  ENV_FILE="$ROOT_ENV_FILE"
elif [ -f "$LOCAL_ENV_FILE" ]; then
  ENV_FILE="$LOCAL_ENV_FILE"
fi
if [ -n "$ENV_FILE" ]; then
  echo "🔧 Loading config from ${ENV_FILE}..."
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "⚠️  Warning: no .env file found at ${ROOT_ENV_FILE} or ${LOCAL_ENV_FILE}. Using default environment variables."
fi

VENV_DIR="${SCRIPT_DIR}/.venv"
RECREATE_VENV=0
if [ ! -d "$VENV_DIR" ] || [ ! -x "$VENV_DIR/bin/python" ]; then
  RECREATE_VENV=1
else
  VENV_PREFIX="$($VENV_DIR/bin/python -c 'import os, sys; print(os.path.realpath(sys.prefix))' 2>/dev/null || true)"
  if [ "$VENV_PREFIX" != "$VENV_DIR" ]; then
    echo "♻️  Detected stale virtual environment (prefix: ${VENV_PREFIX}). Recreating..."
    RECREATE_VENV=1
  fi
fi
if [ "$RECREATE_VENV" -eq 1 ]; then
  rm -rf "$VENV_DIR"
  echo "📦 Creating virtual environment at ${VENV_DIR}..."
  python3 -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"

echo "📥 Installing requirements..."
"$VENV_PYTHON" -m pip install -q -r requirements.txt

echo "🧪 Running Event Loader tests..."
"$VENV_PYTHON" -m pytest -v "$@"
