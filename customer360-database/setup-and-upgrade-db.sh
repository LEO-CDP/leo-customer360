#!/usr/bin/env bash
# Initialize or upgrade a local Customer 360 PostgreSQL database.
#
# Usage:
#   ./setup-and-upgrade-db.sh [--dry-run] [--skip-migrations]
#
# Connection values are read from the repository .env file when present:
#   DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

DRY_RUN=false
SKIP_MIGRATIONS=false

usage() {
	sed -n '2,10p' "$0" | sed 's/^#\{0,1\}[[:space:]]*//'
}

for argument in "$@"; do
	case "$argument" in
		--dry-run) DRY_RUN=true ;;
		--skip-migrations) SKIP_MIGRATIONS=true ;;
		--help|-h) usage; exit 0 ;;
		*) printf 'Unknown argument: %s\n\n' "$argument" >&2; usage >&2; exit 2 ;;
	esac
done

if [[ -f "$ENV_FILE" ]]; then
	set -a
	# shellcheck disable=SC1090
	source "$ENV_FILE"
	set +a
fi

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-${POSTGRES_HOST_PORT:-5432}}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-customer360}"
DB_PASSWORD="${DB_PASSWORD:-}"
WAIT_SECONDS="${DB_WAIT_SECONDS:-60}"

if ! command -v psql >/dev/null 2>&1 || ! command -v pg_isready >/dev/null 2>&1; then
	printf '%s\n' 'Error: psql and pg_isready are required. Install the PostgreSQL client first.' >&2
	exit 1
fi

for value_name in DB_HOST DB_PORT DB_USER DB_NAME WAIT_SECONDS; do
	value="${!value_name}"
	if [[ -z "$value" || "$value" == *$'\n'* ]]; then
		printf 'Error: %s must be a non-empty single-line value.\n' "$value_name" >&2
		exit 1
	fi
done

if ! [[ "$DB_PORT" =~ ^[0-9]+$ && "$WAIT_SECONDS" =~ ^[0-9]+$ ]]; then
	printf '%s\n' 'Error: DB_PORT and DB_WAIT_SECONDS must be non-negative integers.' >&2
	exit 1
fi

if ! [[ "$DB_NAME" =~ ^[A-Za-z_][A-Za-z0-9_$-]*$ && "$DB_USER" =~ ^[A-Za-z_][A-Za-z0-9_$-]*$ ]]; then
	printf '%s\n' 'Error: DB_NAME and DB_USER contain unsupported PostgreSQL identifier characters.' >&2
	exit 1
fi

export PGPASSWORD="$DB_PASSWORD"

PSQL=(psql -X -v ON_ERROR_STOP=1 -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME")
PSQL_ADMIN=(psql -X -v ON_ERROR_STOP=1 -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres)

sql_files=(
	"$PROJECT_ROOT/postgres/init/00-extensions.sql"
	"$SCRIPT_DIR/database-schema.sql"
	"$SCRIPT_DIR/init-cdp-ai-agents.sql"
	"$SCRIPT_DIR/init-core-database.sql"
	"$SCRIPT_DIR/data-view-for-llm.sql"
)

if [[ "$SKIP_MIGRATIONS" == false ]]; then
	while IFS= read -r migration; do
		sql_files+=("$migration")
	done < <(find "$SCRIPT_DIR/migrations" -maxdepth 1 -type f -name '*.sql' ! -name '*.down.sql' | sort)
fi

for sql_file in "${sql_files[@]}"; do
	if [[ ! -f "$sql_file" ]]; then
		printf 'Error: required SQL file not found: %s\n' "$sql_file" >&2
		exit 1
	fi
done

printf '[DATABASE] Target: %s@%s:%s/%s\n' "$DB_USER" "$DB_HOST" "$DB_PORT" "$DB_NAME"
printf '[DATABASE] Planned SQL files: %s\n' "${#sql_files[@]}"

if [[ "$DRY_RUN" == true ]]; then
	printf '%s\n' '[DATABASE] Dry run; no database connection or SQL changes performed.'
	printf '  %s\n' "${sql_files[@]}"
	exit 0
fi

printf '[DATABASE] Waiting up to %ss for PostgreSQL...\n' "$WAIT_SECONDS"
for ((attempt = 1; attempt <= WAIT_SECONDS; attempt++)); do
	if pg_isready -q -h "$DB_HOST" -p "$DB_PORT"; then
		break
	fi
	if (( attempt == WAIT_SECONDS )); then
		printf 'Error: PostgreSQL did not become ready at %s:%s.\n' "$DB_HOST" "$DB_PORT" >&2
		exit 1
	fi
	sleep 1
done

if ! "${PSQL_ADMIN[@]}" -Atqc 'SELECT 1' >/dev/null 2>&1; then
	printf '[DATABASE] Could not connect to the postgres maintenance database.\n' >&2
	exit 1
fi

database_exists="$("${PSQL_ADMIN[@]}" -Atq -v database_name="$DB_NAME" -c 'SELECT 1 FROM pg_database WHERE datname = :"database_name"')"
if [[ "$database_exists" != "1" ]]; then
	printf '[DATABASE] Database %s does not exist; creating it...\n' "$DB_NAME"
	"${PSQL_ADMIN[@]}" -v database_name="$DB_NAME" -c 'CREATE DATABASE :"database_name"'
fi

if ! "${PSQL[@]}" -Atqc 'SELECT 1' >/dev/null; then
	printf '[DATABASE] Could not connect to application database %s.\n' "$DB_NAME" >&2
	exit 1
fi

for sql_file in "${sql_files[@]}"; do
	printf '[DATABASE] Applying %s\n' "${sql_file#"$PROJECT_ROOT/"}"
	if ! "${PSQL[@]}" -f "$sql_file"; then
		printf '[DATABASE] Failed while applying %s; stopping.\n' "$sql_file" >&2
		exit 1
	fi
done

unset PGPASSWORD
printf '%s\n' '[DATABASE] Setup and upgrade complete.'