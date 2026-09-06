#!/usr/bin/env bash

set -euo pipefail

usage() {
	cat <<'EOF'
Usage: postgres/dev-db-backup.sh [options]

Create a UTF-8 plain SQL backup of the application schema from the running
customer360 Postgres container.
The default inserts mode is recommended for uploading/importing into UAT.
Comments are excluded so the restore does not require ownership of existing
extensions such as pg_trgm.
Only the application schema is dumped by default, so pgAdmin does not try to
create superuser-only extensions such as postgis_topology.

Options:
	-o, --output <file>      Output .sql file path
	-e, --env-file <file>    Env file path (default: <repo>/.env)
	-c, --container <name>   Postgres container name (default: customer360-postgres)
	-d, --db-name <name>     Database name (default: DB_NAME from env or customer360)
	-u, --db-user <name>     Database user (default: DB_USER from env or postgres)
	-s, --schema <name>      Application schema (default: DB_SCHEMA or customer360)
	-m, --data-mode <mode>   Data format: inserts|copy (default: inserts)
	-h, --help               Show this help

Data modes:
	inserts  Write normal INSERT statements. Most portable; recommended for UAT.
	copy     Write PostgreSQL COPY blocks. Smaller and faster; restore with psql.

Examples:
	# Default: create a portable INSERT-based backup in backups/.
	postgres/dev-db-backup.sh

	# Recommended UAT upload backup with a fixed filename.
	postgres/dev-db-backup.sh --output backups/customer360-uat.sql --data-mode inserts

	# Faster dump for direct PostgreSQL restore with psql.
	postgres/dev-db-backup.sh --output backups/customer360-copy.sql --data-mode copy

	# Use another env file or database connection settings.
	postgres/dev-db-backup.sh --env-file .env.uat --container customer360-postgres \
		--db-name customer360 --db-user postgres

	# The target database must already have required extensions installed by its DBA.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

opt_output=""
opt_env_file="${REPO_ROOT}/.env"
opt_container=""
opt_db_name=""
opt_db_user=""
opt_schema=""
opt_data_mode="inserts"

while [[ $# -gt 0 ]]; do
	case "$1" in
		-o|--output)
			opt_output="${2:-}"
			shift 2
			;;
		-e|--env-file)
			opt_env_file="${2:-}"
			shift 2
			;;
		-c|--container)
			opt_container="${2:-}"
			shift 2
			;;
		-d|--db-name)
			opt_db_name="${2:-}"
			shift 2
			;;
		-u|--db-user)
			opt_db_user="${2:-}"
			shift 2
			;;
		-s|--schema)
			opt_schema="${2:-}"
			shift 2
			;;
		-m|--data-mode)
			opt_data_mode="${2:-}"
			shift 2
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			echo "Unknown option: $1" >&2
			usage
			exit 1
			;;
	esac
done

if [[ ! -f "${opt_env_file}" ]]; then
	echo "Env file not found: ${opt_env_file}" >&2
	exit 1
fi

set -a
# shellcheck disable=SC1090
source "${opt_env_file}"
set +a

container_name="${opt_container:-${POSTGRES_CONTAINER:-customer360-postgres}}"
db_name="${opt_db_name:-${DB_NAME:-customer360}}"
db_user="${opt_db_user:-${DB_USER:-postgres}}"
schema_name="${opt_schema:-${DB_SCHEMA:-customer360}}"
db_password="${DB_PASSWORD:-${PGPASSWORD:-}}"

if [[ -z "${db_password}" ]]; then
	echo "DB_PASSWORD is empty. Set it in ${opt_env_file}." >&2
	exit 1
fi

if [[ "${opt_data_mode}" != "inserts" && "${opt_data_mode}" != "copy" ]]; then
	echo "Invalid --data-mode '${opt_data_mode}'. Use: inserts or copy." >&2
	exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
	echo "docker command not found in PATH." >&2
	exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -Fxq "${container_name}"; then
	echo "Container is not running: ${container_name}" >&2
	echo "Start infra first, for example: ./dev-c360.sh" >&2
	exit 1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
default_output="${REPO_ROOT}/backups/${db_name}-${timestamp}.sql"
output_file="${opt_output:-${default_output}}"
output_dir="$(dirname "${output_file}")"

mkdir -p "${output_dir}"

# Backup may contain sensitive customer data.
umask 077

echo "Creating backup from container: ${container_name}"
echo "Database: ${db_name}"
echo "Output: ${output_file}"
echo "Data mode: ${opt_data_mode}"

pg_dump_data_flags=(--inserts)
if [[ "${opt_data_mode}" == "copy" ]]; then
	pg_dump_data_flags=()
fi

docker exec -i \
	-e PGPASSWORD="${db_password}" \
	"${container_name}" \
	pg_dump \
	--host=127.0.0.1 \
	--port=5432 \
	--username="${db_user}" \
	--dbname="${db_name}" \
	--schema="${schema_name}" \
	--format=plain \
	--encoding=UTF8 \
	--no-owner \
	--no-privileges \
	--no-comments \
	"${pg_dump_data_flags[@]}" \
	> "${output_file}"

sha256sum "${output_file}" > "${output_file}.sha256"

echo "Backup completed successfully."
echo "SHA256: ${output_file}.sha256"
echo "Restore example: psql -h <uat-db-host> -U <uat-user> -d <uat-db-name> -f ${output_file}"
