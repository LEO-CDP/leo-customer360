#!/usr/bin/env bash
# Install the checked-out Customer 360 agent client into local microservice
# virtualenvs.
#
# Usage:
#   ./customer360-agent/install-local.sh
#   ./customer360-agent/install-local.sh --requirements
#   ./customer360-agent/install-local.sh --service customer360-api
#   ./customer360-agent/install-local.sh --service customer360-api --requirements
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_DIR="$SCRIPT_DIR"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_REQUIREMENTS=0

SERVICE_DIRS=(
	"customer360-api"
)

usage() {
	cat >&2 <<'USAGE'
Install the checked-out Customer 360 agent client into local microservice virtualenvs.

Usage:
	./customer360-agent/install-local.sh
	./customer360-agent/install-local.sh --requirements
	./customer360-agent/install-local.sh --service customer360-api
	./customer360-agent/install-local.sh --service customer360-api --requirements
USAGE
}

log() {
	printf '[agent] %s\n' "$*"
}

fail() {
	printf '[agent] ERROR: %s\n' "$*" >&2
	exit 1
}

if [[ "$PYTHON_BIN" == */* ]]; then
	[[ -x "$PYTHON_BIN" ]] || fail "Python executable not found: $PYTHON_BIN"
else
	command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "Python executable not found: $PYTHON_BIN"
fi

TARGETS=()
while [[ $# -gt 0 ]]; do
	case "$1" in
		--requirements)
			INSTALL_REQUIREMENTS=1
			;;
		--service)
			[[ $# -ge 2 ]] || fail "--service requires a path"
			TARGETS+=("$2")
			shift
			;;
		--service=*)
			TARGETS+=("${1#*=}")
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			fail "unknown argument '$1'"
			;;
	esac
	shift
done

if [[ ${#TARGETS[@]} -eq 0 ]]; then
	TARGETS=("${SERVICE_DIRS[@]}")
fi

install_service() {
	local relative_dir="$1"
	local service_dir="$REPO_ROOT/$relative_dir"
	local venv_dir="$service_dir/.venv"
	local python="$venv_dir/bin/python"
	local requirements_file

	[[ -d "$service_dir" ]] || fail "service directory does not exist: $relative_dir"

	if [[ ! -x "$python" ]]; then
		log "creating virtualenv: $relative_dir/.venv"
		"$PYTHON_BIN" -m venv "$venv_dir"
	fi

	log "installing local agent client into $relative_dir/.venv"
	"$python" -m pip install --quiet --upgrade --no-deps --editable "$AGENT_DIR"

	if [[ "$INSTALL_REQUIREMENTS" -eq 1 ]]; then
		requirements_file="$service_dir/requirements.txt"
		if [[ -f "$requirements_file" ]]; then
			log "installing requirements: ${requirements_file#"$REPO_ROOT/"}"
			"$python" -m pip install --quiet --requirement "$requirements_file"
		fi
	fi

	"$python" -c 'import importlib.metadata as metadata; import leo_customer360_agent; print(metadata.version("leo-customer360-agent"))' >/dev/null
}

for target in "${TARGETS[@]}"; do
	install_service "$target"
done

log "installed leo-customer360-agent into ${#TARGETS[@]} microservice environment(s)"
if [[ "$INSTALL_REQUIREMENTS" -eq 0 ]]; then
	log "use --requirements on a fresh environment to install the service dependencies"
fi
