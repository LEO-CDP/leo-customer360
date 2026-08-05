#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"

cd "$SCRIPT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
	echo "Error: python3 is required but not found in PATH."
	exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
	echo "Creating virtual environment in $VENV_DIR"
	python3 -m venv "$VENV_DIR"
fi

echo "Installing dependencies from requirements.txt"
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r requirements.txt

echo "Running appsflyer_faker.py"
"$PYTHON_BIN" appsflyer_faker.py

echo "Running google_analytics_faker.py"
"$PYTHON_BIN" google_analytics_faker.py
