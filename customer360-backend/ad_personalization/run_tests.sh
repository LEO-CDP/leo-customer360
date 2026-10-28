#!/bin/bash
# Runs the Ad Personalisation Python test suite (Dagster jobs for profiles, ad
# statistics, cohorts, identity links, erasure and partition upkeep).
#
# Hermetic by default: the job definitions, schedules, guards and pure logic run
# in-process and the database tests are skipped. To also run the database tests
# (they need a PostgreSQL with the leo_ads schema and personalization-schema.sql
# applied, and always roll back), opt in explicitly against a DISPOSABLE database:
#
#   AD_PERSONALIZATION_ALLOW_DB_TESTS=1 DB_HOST=127.0.0.1 DB_PORT=5432 \
#   DB_USER=postgres DB_PASSWORD=... DB_NAME=customer360 ./run_tests.sh
#
# A repo-root / local .env is loaded when present, like the sibling runners.
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
  # A moved/copied venv can keep stale paths; recreate if sys.prefix no longer matches.
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

echo "📦 Installing local leo-customer360-dao checkout..."
"$VENV_PYTHON" -m pip install -q --no-deps --upgrade ../../customer360-dao

echo "📥 Installing requirements..."
"$VENV_PYTHON" -m pip install -q -r requirements.txt

echo "🧪 Running Ad Personalisation tests..."
"$VENV_PYTHON" -m pytest -v "$@"
