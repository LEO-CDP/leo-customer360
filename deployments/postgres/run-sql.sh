#!/usr/bin/env bash
# Bootstrap the deployed vDB for an env by running, in a fixed dependency order:
#   1) repo  postgres/**/*.sql   (extensions, keycloak db — filename order)
#   2) repo  database-init/*.sql (the app schema) AFTER the init, in the order the
#      project's own postgres/Dockerfile uses: database-schema -> init-core-database,
#      then data-view-for-llm (materialized views) LAST since it reads those tables.
#   ./run-sql.sh <uat|prod>
#
# The DB is PRIVATE (public_access is non-functional on this platform), so psql is run ON
# a bastion inside the VPC over SSH: BASTION=<user>@<ip> (auto-discovered from the sibling
# ../server deployment's floating IP if unset; SSH user via BASTION_USER, key via SSH_KEY).
# psql is ensured on the bastion (installed via apt if missing). Each file is piped over
# stdin with ON_ERROR_STOP=1 so the first failure aborts.
set -euo pipefail
cd "$(dirname "$0")"

ENV="${1:-}"
case "$ENV" in
  uat|prod) ;;
  *) echo "Usage: ./run-sql.sh <uat|prod>"; exit 1 ;;
esac

PG_SQL_DIR="../../postgres"       # repo-root/postgres/**  (extensions, keycloak db) — filename order
APP_SQL_DIR="../../database-init" # the app schema; ORDER MATTERS, so run these known files first:
APP_ORDER=(database-schema.sql init-core-database.sql data-view-for-llm.sql)
MIGRATIONS_DIR="$APP_SQL_DIR/migrations"

# --- creds/config: overlay (non-secret) + .env/terraform.tfvars (secret) ---
if [[ -f .env ]]; then set -a; source ./.env; set +a; fi
ovl="overlays/${ENV}.tfvars"
tfval() { grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" | sed -E 's/.*"([^"]+)".*/\1/' | head -1; }
DB_USER="$(tfval db_username "$ovl")"
DB_NAME="$(tfval db_name "$ovl")"
DB_PASS="${TF_VAR_db_password:-}"
[[ -z "$DB_PASS" && -f terraform.tfvars ]] && DB_PASS="$(tfval db_password terraform.tfvars)"

# --- host/port from terraform outputs of THIS env's workspace ---
terraform workspace select "$ENV" >/dev/null 2>&1 || { echo "ERROR: no '$ENV' workspace (deploy first)."; exit 1; }
HOST="$(terraform output -raw db_host 2>/dev/null || true)"
PORT="$(terraform output -raw db_port 2>/dev/null || true)"
if [[ -z "$HOST" || -z "$PORT" ]]; then echo "ERROR: could not read db_host/db_port from terraform outputs."; exit 1; fi
if [[ -z "$DB_USER" || -z "$DB_NAME" || -z "$DB_PASS" ]]; then
  echo "ERROR: missing db_username/db_name (overlay) or db_password (.env/terraform.tfvars)."; exit 1
fi

# --- resolve the bastion (explicit BASTION wins; else auto-discover ../server's public IP) ---
if [[ -z "${BASTION:-}" ]]; then
  disc="$( (cd ../server && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -json servers 2>/dev/null) || true )"
  if [[ -n "$disc" ]]; then
    bip="$(printf '%s' "$disc" | python3 -c '
import sys, json, re
try: d = json.load(sys.stdin)
except Exception: sys.exit(0)
out = []
for s in (d or {}).values():
    for grp in ("external_interfaces", "internal_interfaces"):
        for iface in (s.get(grp) or []):
            if isinstance(iface, dict):
                for key in ("floating_ip", "ip", "address"):
                    v = iface.get(key)
                    if isinstance(v, str) and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", v) and not v.startswith(("10.", "172.", "192.168.")):
                        out.append(v)
print(out[0] if out else "")' 2>/dev/null)"
    [[ -n "$bip" ]] && BASTION="${BASTION_USER:-leocdp360}@$bip" && echo ">> Auto-discovered bastion: $BASTION"
  fi
fi
[[ -n "${BASTION:-}" ]] || { echo "ERROR: no bastion. Set BASTION=<user>@<floating_ip> (the ../server box) — the DB is private."; exit 1; }

# --- run psql ON the bastion (it reaches the DB's private ip). Ensure psql is installed. ---
SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"
PW_B64="$(printf %s "$DB_PASS" | base64 | tr -d '\n')" # ship the password without quoting headaches
remote="command -v psql >/dev/null 2>&1 || { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-client >/dev/null; }; "
remote+="PGPASSWORD=\$(printf %s '$PW_B64' | base64 -d) psql -v ON_ERROR_STOP=1 -h '$HOST' -p '$PORT' -U '$DB_USER' -d '$DB_NAME' -f -"
# The bastion is disposable and recreated often (host key changes) -> don't pin it.
run_psql() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$BASTION" "$remote"; }

# --- collect scripts in dependency order: postgres/init first, then the app schema ---
FILES=()
while IFS= read -r f; do FILES+=("$f"); done < <(find "$PG_SQL_DIR" -type f -iname '*.sql' | sort)
if [[ -d "$APP_SQL_DIR" ]]; then
  # known app-schema files in dependency order, then any *other* *.sql alphabetically
  for n in "${APP_ORDER[@]}"; do [[ -f "$APP_SQL_DIR/$n" ]] && FILES+=("$APP_SQL_DIR/$n"); done
  while IFS= read -r f; do
    printf '%s\n' "${APP_ORDER[@]}" | grep -qxF "$(basename "$f")" || FILES+=("$f")
  done < <(find "$APP_SQL_DIR" -maxdepth 1 -type f -iname '*.sql' | sort)
fi
if [[ -d "$MIGRATIONS_DIR" ]]; then
  while IFS= read -r f; do FILES+=("$f"); done < <(find "$MIGRATIONS_DIR" -type f -iname '*.sql' | sort)
fi
if [[ ${#FILES[@]} -eq 0 ]]; then echo "No *.sql found under $PG_SQL_DIR or $APP_SQL_DIR — nothing to run."; exit 0; fi
echo "Running ${#FILES[@]} SQL script(s) via psql on ${BASTION} against ${DB_NAME}@${HOST}:${PORT} (user ${DB_USER}):"
for f in "${FILES[@]}"; do
  echo "  -> $f"
  run_psql < "$f"
done
echo "All SQL scripts completed."
