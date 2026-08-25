#!/usr/bin/env bash
# =============================================================================
# Migration drift test — builds a THROWAWAY database with `dbmate up` and diffs
# it against the CURRENT live database (e.g. UAT/prod) to confirm the migrations
# reproduce the live schema and to surface any drift. The test database is
# created and dropped by the test; the live DB is only ever READ.
#
# The reference is REQUIRED — set REFERENCE_DATABASE_URL:
#
#   REFERENCE_DATABASE_URL=postgres://user:pass@host:port/db  ./run-migration-test.sh
#
# Because a live DB holds real transactional data, only the SCHEMA is a hard
# pass/fail gate; per-table row-count / content differences are reported as INFO
# (a fresh dbmate build legitimately has only seed data). All comparisons are
# scoped to schema `customer360`.
#
# The private VPC DB isn't reachable directly — open a tunnel through the bastion
# first, then point the test at it:
#   ssh -fN -L 15432:<db-host>:<db-port> -i ~/.ssh/c360-api_ed25519 <user>@<bastion>
#   REFERENCE_DATABASE_URL="postgres://app_admin:$PW@localhost:15432/customer360?sslmode=prefer" \
#     ./run-migration-test.sh
#
# The db host/port/bastion come from the same terraform outputs run-sql.sh uses
# (deployments/postgres, workspace uat/prod) — run from an env with the AWS creds
# + bastion access (e.g. the CD runner), not a bare laptop.
#
#   KEEP=1        keep the container + artifacts for debugging
#   PG_IMAGE=…    Postgres image w/ PostGIS+pgvector (default: build the repo image)
#   DBMATE_IMAGE= dbmate image (default ghcr.io/amacneil/dbmate:2)
#
# Requires: docker. On Windows run via Git Bash.
# =============================================================================
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git-Bash: don't rewrite in-container paths

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
DB_INIT_DIR="$REPO_ROOT/database-init"
OUT_DIR="$SCRIPT_DIR/.artifacts"

PG_IMAGE="${PG_IMAGE:-customer360-postgres:migtest}"
DBMATE_IMAGE="${DBMATE_IMAGE:-ghcr.io/amacneil/dbmate:2}"
SCHEMA="customer360"
PGPASS="migtest"

SUF="$$"; NET="migtest-net-$SUF"; PG="migtest-pg-$SUF"

C_GREEN=$'\033[0;32m'; C_RED=$'\033[0;31m'; C_YEL=$'\033[0;33m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'
ok()   { echo "${C_GREEN}[ok]${C_RST}    $*"; }
info() { echo "${C_DIM}[..]${C_RST}    $*"; }
warn() { echo "${C_YEL}[warn]${C_RST}  $*"; }
fail() { echo "${C_RED}[FAIL]${C_RST}  $*"; }

cleanup() {
  if [[ "${KEEP:-0}" = "1" ]]; then
    echo "KEEP=1 -> leaving container '$PG' and artifacts in $OUT_DIR"; return
  fi
  docker rm -f "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$OUT_DIR" 2>/dev/null || true
}
trap cleanup EXIT
mkdir -p "$OUT_DIR"

# --- sanity ---
command -v docker >/dev/null 2>&1 || { fail "docker not found on PATH"; exit 2; }
[[ -d "$DB_INIT_DIR/db/migrations" ]] || { fail "missing $DB_INIT_DIR/db/migrations"; exit 2; }
[[ -n "${REFERENCE_DATABASE_URL:-}" ]] || { fail "REFERENCE_DATABASE_URL is required — open a bastion tunnel to the current DB and point this at it (see header)."; exit 2; }

# --- ensure a Postgres image with the required extensions ---
if ! docker image inspect "$PG_IMAGE" >/dev/null 2>&1; then
  info "building Postgres image ($PG_IMAGE) with PostGIS + pgvector ..."
  docker build -q -f "$REPO_ROOT/postgres/Dockerfile" -t "$PG_IMAGE" "$REPO_ROOT" >/dev/null
fi

# --- disposable Postgres (hosts the dbmate CANDIDATE build) ---
info "starting throwaway Postgres '$PG' ..."
docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" \
  -e POSTGRES_PASSWORD="$PGPASS" -e POSTGRES_DB=postgres "$PG_IMAGE" >/dev/null
for _ in $(seq 1 60); do
  docker exec "$PG" pg_isready -U postgres -d postgres >/dev/null 2>&1 && break; sleep 2
done
docker exec "$PG" pg_isready -U postgres -d postgres >/dev/null 2>&1 || { fail "Postgres not ready"; exit 2; }
ok "Postgres ready"

psql_db() { docker exec -i "$PG" psql -v ON_ERROR_STOP=1 -U postgres -d "$1" -tAqc "$2"; }

# --- CANDIDATE: dbmate up into a throwaway db ---
# Docker bind-mount source must be a Windows path on Git-Bash/Docker-Desktop
# (a `/c/Users/...` MSYS path mounts empty inside Docker's VM); `pwd -W` yields
# `C:/Users/...`, and falls back to the plain absolute path on Linux.
MIG_MOUNT="$( { cd "$DB_INIT_DIR/db" && pwd -W; } 2>/dev/null || echo "$DB_INIT_DIR/db" )"
CAND_DB="cand_db"
info "building CANDIDATE ($CAND_DB) via 'dbmate up' (mount: $MIG_MOUNT) ..."
docker exec "$PG" psql -U postgres -qc "CREATE DATABASE $CAND_DB" >/dev/null
docker run --rm --network "$NET" -v "$MIG_MOUNT:/db:ro" \
  -e DATABASE_URL="postgres://postgres:$PGPASS@$PG:5432/$CAND_DB?sslmode=disable" \
  "$DBMATE_IMAGE" --no-dump-schema --migrations-dir /db/migrations up
ok "candidate built"
cand_query() { psql_db "$CAND_DB" "$1"; }

# --- REFERENCE = the live current DB via REFERENCE_DATABASE_URL (READ-ONLY) ---
info "REFERENCE = live DB via REFERENCE_DATABASE_URL (READ-ONLY)"
# --network host so a localhost SSH tunnel (or any reachable host) works.
ref_query() {
  docker run --rm --network host "$PG_IMAGE" \
    psql -v ON_ERROR_STOP=1 "$REFERENCE_DATABASE_URL" -tAqc "$1"
}
ref_query "SELECT 1" >/dev/null 2>&1 || { fail "cannot reach REFERENCE_DATABASE_URL (open a tunnel to the private DB first)"; exit 2; }
ok "live reference reachable"

# =============================================================================
# Comparison
# =============================================================================
HARD_FAILS=0; SOFT_DIFFS=0

# compare_query <label> <sql> <gate>   gate = hard | soft
compare_query() {
  local label="$1" sql="$2" gate="$3"
  ref_query  "$sql" 2>/dev/null | sort > "$OUT_DIR/ref.$label.txt"  || true
  cand_query "$sql" 2>/dev/null | sort > "$OUT_DIR/cand.$label.txt" || true
  if diff -u "$OUT_DIR/ref.$label.txt" "$OUT_DIR/cand.$label.txt" > "$OUT_DIR/diff.$label.txt"; then
    ok "$label identical"
  elif [[ "$gate" = "soft" ]]; then
    warn "$label differs (informational in live mode):"
    sed 's/^/         /' "$OUT_DIR/diff.$label.txt" | head -20
    SOFT_DIFFS=$((SOFT_DIFFS+1))
  else
    fail "$label differs:"
    sed 's/^/         /' "$OUT_DIR/diff.$label.txt" | head -40
    HARD_FAILS=$((HARD_FAILS+1))
  fi
}

echo ""; echo "=== SCHEMA comparison (hard gate, schema=$SCHEMA) ==="
compare_query "tables" "SELECT table_name||'|'||table_type FROM information_schema.tables WHERE table_schema='$SCHEMA';" hard
compare_query "columns" "SELECT table_name||'|'||column_name||'|'||data_type||'|'||coalesce(character_maximum_length::text,'')||'|'||is_nullable||'|'||coalesce(column_default,'') FROM information_schema.columns WHERE table_schema='$SCHEMA';" hard
# Exclude PG16 auto NOT NULL check-constraints: their names are OID-based
# (e.g. 25344_26793_1_not_null) and so differ between any two DBs. Column
# nullability is already covered by the `columns` comparison above.
compare_query "constraints" "SELECT tc.table_name||'|'||tc.constraint_type||'|'||coalesce(cc.check_clause,'')||'|'||tc.constraint_name FROM information_schema.table_constraints tc LEFT JOIN information_schema.check_constraints cc ON cc.constraint_name=tc.constraint_name AND cc.constraint_schema=tc.constraint_schema WHERE tc.table_schema='$SCHEMA' AND tc.constraint_name NOT LIKE '%\_not\_null' ESCAPE '\';" hard
compare_query "indexes" "SELECT tablename||'|'||indexname||'|'||regexp_replace(indexdef,'^CREATE','C') FROM pg_indexes WHERE schemaname='$SCHEMA';" hard
compare_query "functions" "SELECT p.proname||'|'||pg_get_function_identity_arguments(p.oid)||'|'||p.prokind FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='$SCHEMA';" hard
compare_query "triggers" "SELECT event_object_table||'|'||trigger_name||'|'||event_manipulation||'|'||action_timing||'|'||action_statement FROM information_schema.triggers WHERE trigger_schema='$SCHEMA';" hard
compare_query "rls_policies" "SELECT tablename||'|'||policyname||'|'||cmd||'|'||coalesce(qual,'')||'|'||coalesce(with_check,'') FROM pg_policies WHERE schemaname='$SCHEMA';" hard
compare_query "rls_flags" "SELECT c.relname||'|'||c.relrowsecurity||'|'||c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='$SCHEMA' AND c.relkind='r';" hard
compare_query "matviews" "SELECT matviewname FROM pg_matviews WHERE schemaname='$SCHEMA';" hard

# Data gate is soft: a live DB holds real transactional data a fresh build lacks.
DATA_GATE="soft"
echo ""; echo "=== DATA comparison (${DATA_GATE} gate, schema=$SCHEMA) ==="
ROWCOUNT_SQL="SELECT string_agg(fmt,E'\n' ORDER BY fmt) FROM (SELECT format('SELECT %L||''|''||count(*) FROM %I.%I', c.relname, n.nspname, c.relname) AS fmt FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='$SCHEMA' AND c.relkind='r') s;"
CHECKSUM_SQL="SELECT string_agg(fmt,E'\n' ORDER BY fmt) FROM (SELECT format('SELECT %L||''|''||coalesce(md5(string_agg(t.line,E''\n'' ORDER BY t.line)),''<empty>'') FROM (SELECT (x.*)::text AS line FROM %I.%I x) t', c.relname, n.nspname, c.relname) AS fmt FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='$SCHEMA' AND c.relkind='r') s;"
compare_query "row_counts" "$(cand_query "$ROWCOUNT_SQL")" "$DATA_GATE"
compare_query "content_md5" "$(cand_query "$CHECKSUM_SQL")" "$DATA_GATE"

# =============================================================================
echo ""
[[ "$SOFT_DIFFS" -gt 0 ]] && warn "$SOFT_DIFFS data group(s) differ — expected against a live DB (real transactional data); review the diffs above."
if [[ "$HARD_FAILS" -eq 0 ]]; then
  ok "MIGRATION PARITY VERIFIED — dbmate schema matches the live reference."
  echo "${C_GREEN}PASS${C_RST}"; exit 0
else
  fail "$HARD_FAILS hard comparison group(s) differ (see diffs; rerun with KEEP=1 to inspect $OUT_DIR)."
  echo "${C_RED}FAIL${C_RST}"; exit 1
fi
