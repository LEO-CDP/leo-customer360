#!/usr/bin/env bash
# Bootstrap / migrate the deployed vDB for an env, in two stages:
#   1) repo postgres/**/*.sql via psql (extensions, keycloak db — filename order).
#      These are infra bootstrap, NOT app schema, so they stay on psql.
#   2) app schema via dbmate `up` (database-init/db/migrations/*.sql, tracked in
#      the schema_migrations table). Ordered, idempotent and version-tracked --
#      replaces the old blind raw replay of database-schema.sql / init-core /
#      data-view + database-init/migrations, which had no version tracking.
#   ./run-sql.sh <uat|prod>
#
# The DB is PRIVATE (public_access is non-functional on this platform), so BOTH
# psql and dbmate run ON a bastion inside the VPC over SSH: BASTION=<user>@<ip>
# (auto-discovered from the sibling ../server deployment's floating IP if unset;
# SSH user via BASTION_USER, key via SSH_KEY). psql and the dbmate static binary
# are installed on the bastion on demand. dbmate runs ONLY up-migrations here.
# NEVER feed the dbmate migration files to psql -- their `-- migrate:down`
# sections contain `DROP SCHEMA customer360 CASCADE`.
set -euo pipefail
cd "$(dirname "$0")"

ENV="${1:-}"
case "$ENV" in
  uat|prod) ;;
  *) echo "Usage: ./run-sql.sh <uat|prod>"; exit 1 ;;
esac

PG_SQL_DIR="../../postgres"          # repo-root/postgres/** (extensions, keycloak db) — psql, filename order
MIG_DIR="../../database-init/db/migrations"  # app schema, applied by dbmate (tracked in schema_migrations)

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

# --- ssh/scp onto the bastion (it reaches the DB's private ip) ---
SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"
# The bastion is disposable and recreated often (host key changes) -> don't pin it.
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
bssh() { ssh "${SSH_OPTS[@]}" "$BASTION" "$@"; }
bscp() { scp "${SSH_OPTS[@]}" "$@"; }

# psql on the bastion (installed on demand); files piped over stdin, first error aborts.
PW_B64="$(printf %s "$DB_PASS" | base64 | tr -d '\n')" # ship the password without quoting headaches
remote="command -v psql >/dev/null 2>&1 || { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-client >/dev/null; }; "
remote+="PGPASSWORD=\$(printf %s '$PW_B64' | base64 -d) psql -v ON_ERROR_STOP=1 -h '$HOST' -p '$PORT' -U '$DB_USER' -d '$DB_NAME' -f -"
run_psql() { bssh "$remote"; }

# --- stage 1: infra SQL (extensions, keycloak db) via psql — NOT dbmate-managed ---
INFRA_FILES=()
while IFS= read -r f; do INFRA_FILES+=("$f"); done < <(find "$PG_SQL_DIR" -type f -iname '*.sql' | sort)
if [[ ${#INFRA_FILES[@]} -gt 0 ]]; then
  echo "Applying ${#INFRA_FILES[@]} infra SQL script(s) via psql on ${BASTION} against ${DB_NAME}@${HOST}:${PORT}:"
  for f in "${INFRA_FILES[@]}"; do echo "  -> $f"; run_psql < "$f"; done
fi

# --- stage 2: app schema via dbmate `up` on the bastion (tracked, idempotent) ---
[[ -d "$MIG_DIR" ]] || { echo "ERROR: migrations dir not found: $MIG_DIR"; exit 1; }
mig_count="$(find "$MIG_DIR" -maxdepth 1 -type f -iname '*.sql' | wc -l | tr -d ' ')"
[[ "$mig_count" -gt 0 ]] || { echo "ERROR: no *.sql migrations under $MIG_DIR"; exit 1; }

# Build DATABASE_URL locally (python3 percent-encodes user+password) and ship it
# base64 so no secret lands on the SSH command line / bastion process list.
dburl="$(python3 - "$DB_USER" "$DB_PASS" "$HOST" "$PORT" "$DB_NAME" <<'PY'
import sys, urllib.parse as u
usr, pw, host, port, db = sys.argv[1:6]
print(f"postgres://{u.quote(usr, safe='')}:{u.quote(pw, safe='')}@{host}:{port}/{db}?sslmode=prefer")
PY
)"
dburl_b64="$(printf %s "$dburl" | base64 | tr -d '\n')"

remote_tmp="/tmp/c360-dbmate.$$"
echo "Shipping ${mig_count} migration(s) to ${BASTION}:${remote_tmp} and running 'dbmate up':"
bssh "mkdir -p '$remote_tmp/migrations'"
bscp "$MIG_DIR"/*.sql "$BASTION:$remote_tmp/migrations/"
# On the bastion: ensure curl + the dbmate static binary (arch-matched), then apply.
# Only up-migrations run; --wait blocks until the DB accepts connections. The temp
# dir is always removed and dbmate's exit code is propagated back over SSH.
bssh "
set -u
command -v curl >/dev/null 2>&1 || { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl >/dev/null; } || { echo 'curl install failed'; exit 1; }
dbm=\$(command -v dbmate || true)
if [ -z \"\$dbm\" ]; then
  case \"\$(uname -m)\" in x86_64) a=amd64 ;; aarch64|arm64) a=arm64 ;; *) echo \"unsupported arch \$(uname -m)\"; exit 1 ;; esac
  curl -fsSL -o '$remote_tmp/dbmate' \"https://github.com/amacneil/dbmate/releases/latest/download/dbmate-linux-\$a\" || { echo 'dbmate download failed'; rm -rf '$remote_tmp'; exit 1; }
  chmod +x '$remote_tmp/dbmate'; dbm='$remote_tmp/dbmate'
fi
export DATABASE_URL=\$(printf %s '$dburl_b64' | base64 -d)
\"\$dbm\" --no-dump-schema --wait --migrations-dir '$remote_tmp/migrations' up
rc=\$?
rm -rf '$remote_tmp'
exit \$rc
"
echo "Schema migrations complete."
