#!/usr/bin/env bash
# Install the checked-out Customer 360 DAO into local microservice virtualenvs.
#
# Usage:
#   ./customer360-dao/install-local.sh
#   ./customer360-dao/install-local.sh --requirements
#   ./customer360-dao/install-local.sh --service customer360-api
#   ./customer360-dao/install-local.sh --service customer360-backend/segmentation --requirements
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DAO_DIR="$SCRIPT_DIR"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_REQUIREMENTS=0

# These are the repository's Python microservice roots. customer360-backend child
# locations get their own environment because their test/start scripts support
# both shared and per-location virtualenvs.
SERVICE_DIRS=(
    "customer360-api"
    "customer360-event-api"
    "ads-server"
    "customer360-backend"
)
while IFS= read -r service_dir; do
    SERVICE_DIRS+=("${service_dir#"$REPO_ROOT/"}")
done < <(find "$REPO_ROOT/customer360-backend" -mindepth 2 -maxdepth 2 -type f -name 'requirements.txt' -printf '%h\n' | sort -u)

usage() {
        cat >&2 <<'USAGE'
Install the checked-out Customer 360 DAO into local microservice virtualenvs.

Usage:
    ./customer360-dao/install-local.sh
    ./customer360-dao/install-local.sh --requirements
    ./customer360-dao/install-local.sh --service customer360-api
    ./customer360-dao/install-local.sh --service customer360-backend/segmentation --requirements
USAGE
}

log() {
    printf '[dao] %s\n' "$*"
}

fail() {
    printf '[dao] ERROR: %s\n' "$*" >&2
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

    log "installing local DAO into $relative_dir/.venv"
    "$python" -m pip install --quiet --upgrade --no-deps --editable "$DAO_DIR"

    if [[ "$INSTALL_REQUIREMENTS" -eq 1 ]]; then
        if [[ "$relative_dir" == "customer360-backend" ]]; then
            mapfile -t requirements_files < <(
                find "$service_dir" -maxdepth 2 -type f -name 'requirements*.txt' -print | sort
            )
        else
            requirements_files=("$service_dir/requirements.txt")
        fi

        for requirements_file in "${requirements_files[@]}"; do
            [[ -f "$requirements_file" ]] || continue
            log "installing requirements: ${requirements_file#"$REPO_ROOT/"}"
            "$python" -m pip install --quiet --requirement "$requirements_file"
        done
    fi

    "$python" -c 'import leo_customer360_dao; print(leo_customer360_dao.__version__)' >/dev/null
}

for target in "${TARGETS[@]}"; do
    install_service "$target"
done

log "installed leo-customer360-dao into ${#TARGETS[@]} microservice environment(s)"
if [[ "$INSTALL_REQUIREMENTS" -eq 0 ]]; then
    log "use --requirements on a fresh environment to install each service's dependencies"
fi
