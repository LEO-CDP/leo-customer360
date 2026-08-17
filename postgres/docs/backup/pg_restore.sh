#!/usr/bin/env bash
# =============================================================================
# pg_restore.sh — restore a logical backup produced by pg_backup.sh.
#
#   ./pg_restore.sh <backup-dir>
#     <backup-dir>  local dir containing globals.sql + <db>.dir.tar / <db>.dump
#                   (or set S3_SOURCE=s3://bucket/prefix/TS to fetch first).
#
# Order: globals (roles) -> each database. Restores WITH the pgvector index-
# rebuild knobs pre-set (maintenance_work_mem + parallel workers) because HNSW/
# IVFFlat indexes are REBUILT on logical restore — see ../03-pgvector-backup.md.
#
# The TARGET must carry the same extensions (postgis + pgvector >= source):
# use an image built from ../../Dockerfile, NOT stock postgis/postgis:16-3.5.
#
# DESTRUCTIVE: with DROP_EXISTING=true it recreates databases. Point PG* at the
# RESTORE target, not production, and dry-run a drill first (../02 runbook).
# =============================================================================
set -Eeuo pipefail

PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-postgres}"
: "${PGPASSWORD:?PGPASSWORD must be set}"
export PGHOST PGPORT PGUSER PGPASSWORD

DATABASES="${DATABASES:-customer360 db_keycloak}"
JOBS="${JOBS:-4}"
DROP_EXISTING="${DROP_EXISTING:-false}"      # true => --clean --if-exists --create

# pgvector index-rebuild tuning for the restore session (see ../03).
RESTORE_MAINT_WORK_MEM="${RESTORE_MAINT_WORK_MEM:-2GB}"
RESTORE_PARALLEL_MAINT_WORKERS="${RESTORE_PARALLEL_MAINT_WORKERS:-4}"
export PGOPTIONS="-c maintenance_work_mem=${RESTORE_MAINT_WORK_MEM} -c max_parallel_maintenance_workers=${RESTORE_PARALLEL_MAINT_WORKERS}"

SRC="${1:-}"
S3_SOURCE="${S3_SOURCE:-}"
S3_ENDPOINT="${S3_ENDPOINT:-}"

log() { echo "[pg_restore] $*"; }
die() { echo "[pg_restore] ERROR: $*" >&2; exit 1; }
trap 'die "failed at line ${LINENO}"' ERR

# --- Fetch from S3/vStorage if requested -------------------------------------
if [ -n "${S3_SOURCE}" ]; then
  command -v aws >/dev/null || die "aws-cli not found but S3_SOURCE set"
  [ -n "${S3_ENDPOINT}" ] || die "set S3_ENDPOINT to use S3_SOURCE"
  aws configure set default.s3.addressing_style path 2>/dev/null || true
  SRC="${SRC:-/tmp/pgrestore-$$}"; mkdir -p "${SRC}"
  log "fetching ${S3_SOURCE} -> ${SRC}"
  aws --endpoint-url "${S3_ENDPOINT}" s3 cp "${S3_SOURCE%/}/" "${SRC}/" --recursive
fi

[ -n "${SRC}" ] && [ -d "${SRC}" ] || die "usage: $0 <backup-dir>  (or set S3_SOURCE)"

# --- Verify checksums if present ---------------------------------------------
if [ -f "${SRC}/SHA256SUMS" ]; then
  log "verifying SHA256SUMS..."
  ( cd "${SRC}" && sha256sum -c SHA256SUMS ) || die "checksum verification failed"
fi

# --- 1. Globals first (roles must exist before object ownership resolves) ----
if [ -f "${SRC}/globals.sql" ]; then
  log "restoring globals (roles/tablespaces)..."
  psql -d postgres -v ON_ERROR_STOP=0 -f "${SRC}/globals.sql" \
    || log "WARN: some globals errored (often 'role already exists') — continuing"
fi

# --- 2. Each database --------------------------------------------------------
common=(--no-owner --no-privileges --jobs="${JOBS}")
[ "${DROP_EXISTING}" = "true" ] && common+=(--clean --if-exists --create)

for db in ${DATABASES}; do
  log "restoring '${db}' (maintenance_work_mem=${RESTORE_MAINT_WORK_MEM}, parallel_maint=${RESTORE_PARALLEL_MAINT_WORKERS})..."

  # Ensure the DB exists when not using --create.
  if [ "${DROP_EXISTING}" != "true" ]; then
    psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${db}'" | grep -q 1 \
      || psql -d postgres -c "CREATE DATABASE ${db}"
  fi
  target_db="${db}"; [ "${DROP_EXISTING}" = "true" ] && target_db="postgres"  # --create needs a maintenance DB

  if [ -f "${SRC}/${db}.dir.tar" ]; then
    tar -C "${SRC}" -xf "${SRC}/${db}.dir.tar"
    pg_restore -d "${target_db}" "${common[@]}" "${SRC}/${db}.dir" \
      || die "pg_restore failed for ${db}"
    rm -rf "${SRC}/${db}.dir"
  elif [ -f "${SRC}/${db}.dump" ]; then
    pg_restore -d "${target_db}" "${common[@]}" "${SRC}/${db}.dump" \
      || die "pg_restore failed for ${db}"
  else
    die "no artifact for '${db}' in ${SRC} (expected ${db}.dir.tar or ${db}.dump)"
  fi
  log "  -> ${db} restored"
done

# --- 3. Post-restore sanity (extensions must be present & usable) ------------
log "post-restore extension check on '${DATABASES%% *}':"
psql -d "${DATABASES%% *}" -c \
  "SELECT extname, extversion FROM pg_extension ORDER BY 1;"

log "DONE. Run the app-level checks in ../02-backup-and-recovery.md#restore-drill-runbook"
