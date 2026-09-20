#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"

if [[ -f "$ENV_FILE" ]]; then
	set -a
	# shellcheck disable=SC1090
	source "$ENV_FILE"
	set +a
fi

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
	echo "ERROR: Python executable not found: $PYTHON_BIN" >&2
	exit 1
}

ARGS=(
	--url "${UAT_TRACKING_API_URL:-https://beta.leocdp.com/data/api/v1/tracking/logs}"
	--data-source-id "${UAT_TRACKING_DATA_SOURCE_ID:-4512a4ab-9fe8-4a1a-9915-521fdaf9925a}"
	--sessions "${UAT_SESSIONS:-25}"
	--min-events "${UAT_MIN_EVENTS:-3}"
	--max-events "${UAT_MAX_EVENTS:-7}"
	--lookback-hours "${UAT_LOOKBACK_HOURS:-24}"
	--concurrency "${UAT_CONCURRENCY:-2}"
	--timeout "${UAT_REQUEST_TIMEOUT_SECONDS:-15}"
	--retries "${UAT_RETRIES:-2}"
	--verbose
)

if [[ -n "${UAT_RANDOM_SEED:-}" ]]; then
	ARGS+=(--seed "$UAT_RANDOM_SEED")
fi

if [[ "${UAT_DRY_RUN:-false}" == "true" ]]; then
	ARGS+=(--dry-run)
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/uat_tracking_traffic_simulator.py" "${ARGS[@]}" "$@"