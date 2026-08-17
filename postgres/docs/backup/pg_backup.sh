#!/usr/bin/env bash
# =============================================================================
# pg_backup.sh — logical backup of the Customer360 PostgreSQL tier.
#
# Backs up (in one run):
#   1. GLOBALS  — roles/tablespaces via `pg_dumpall --globals-only`
#                 (pg_dump does NOT dump roles; restore these first).
#   2. Each DB  — `customer360` + `db_keycloak` by default, directory format,
#                 parallel (-j), zstd-compressed, then tar'd into one artifact.
#
# pgvector note: vector DATA dumps fine here; vector INDEXES (HNSW/IVFFlat) are
# rebuilt on restore — see pg_restore.sh and ../03-pgvector-backup.md. For very
# large vector tables prefer a PHYSICAL backup (pgBackRest); this logical track
# is for portability, per-tenant extraction, and corruption insurance.
#
# Verifies each artifact (`pg_restore --list`), writes SHA256SUMS, optionally
# offloads to vStorage/S3, and prunes local backups older than RETENTION_DAYS.
#
# Everything is env-driven (see ./.env.example). Safe to run from a cron/CronJob
# or the Compose sidecar. Exit non-zero on ANY failure (so cron/K8s can alert).
# =============================================================================
set -Eeuo pipefail

# --- Config (env with project-matching defaults) -----------------------------
PGHOST="${PGHOST:-postgres}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-postgres}"
# PGPASSWORD must be supplied by the environment (Secret / .env). Never hard-code.
: "${PGPASSWORD:?PGPASSWORD must be set (from a K8s Secret or .env)}"
export PGHOST PGPORT PGUSER PGPASSWORD

# Space-separated list of databases to back up.
DATABASES="${DATABASES:-customer360 db_keycloak}"

# directory (parallel dump+restore) | custom (single file, serial dump).
PG_DUMP_FORMAT="${PG_DUMP_FORMAT:-directory}"
JOBS="${JOBS:-4}"                       # parallel workers (directory format only)
COMPRESS="${COMPRESS:-zstd}"            # zstd | lz4 | gzip | none  (per-tool support varies)

BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"   # local pruning; set S3 lifecycle for offsite

# --- S3 / vStorage offload (optional) ----------------------------------------
# Set S3_ENDPOINT + S3_BUCKET to enable. Uses aws-cli (path-style for non-AWS).
#   S3_ENDPOINT   e.g. https://hcm04.vstorage.vngcloud.vn
#   S3_BUCKET     e.g. c360-prod-backups
#   S3_PREFIX     e.g. postgres  (key prefix inside the bucket)
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION (default us-east-1)
S3_ENDPOINT="${S3_ENDPOINT:-}"
S3_BUCKET="${S3_BUCKET:-}"
S3_PREFIX="${S3_PREFIX:-postgres}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="${AWS_REGION}"

# --- Derived ------------------------------------------------------------------
TS="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BACKUP_ROOT}/${TS}"
LOG_PREFIX="[pg_backup ${TS}]"

log()  { echo "${LOG_PREFIX} $*"; }
die()  { echo "${LOG_PREFIX} ERROR: $*" >&2; exit 1; }
trap 'die "failed at line ${LINENO}"' ERR

command -v pg_dump    >/dev/null || die "pg_dump not found on PATH"
command -v pg_dumpall >/dev/null || die "pg_dumpall not found on PATH"

# --- Preflight ----------------------------------------------------------------
log "waiting for ${PGHOST}:${PGPORT} to accept connections..."
for i in $(seq 1 30); do
  if pg_isready -q; then break; fi
  [ "$i" -eq 30 ] && die "database not ready after 30 attempts"
  sleep 2
done

mkdir -p "${DEST}"
log "backup destination: ${DEST}"
log "server version: $(psql -tAc 'SHOW server_version' || echo '?')"
log "pgvector version: $(psql -d "${DATABASES%% *}" -tAc \
      "SELECT extversion FROM pg_extension WHERE extname='vector'" 2>/dev/null || echo 'n/a')"

# --- 1. Globals (roles/tablespaces) ------------------------------------------
log "dumping globals (roles/tablespaces, incl. role passwords)..."
pg_dumpall --globals-only > "${DEST}/globals.sql" \
  || die "pg_dumpall --globals-only failed"

# --- 2. Per-database dumps ----------------------------------------------------
for db in ${DATABASES}; do
  log "dumping database '${db}' (format=${PG_DUMP_FORMAT})..."
  case "${PG_DUMP_FORMAT}" in
    directory)
      outdir="${DEST}/${db}.dir"
      pg_dump -d "${db}" --format=directory --jobs="${JOBS}" \
              --compress="${COMPRESS}" --file="${outdir}" \
        || die "pg_dump (directory) failed for ${db}"
      # Bundle the directory into one artifact for easy offload (already compressed).
      tar -C "${DEST}" -cf "${DEST}/${db}.dir.tar" "${db}.dir"
      rm -rf "${outdir}"
      artifact="${db}.dir.tar"
      # Verify: TOC must be readable.
      tar -C "${DEST}" -xf "${DEST}/${artifact}" \
        && pg_restore --list "${DEST}/${db}.dir" >/dev/null \
        && rm -rf "${DEST}/${db}.dir" \
        || die "verify (pg_restore --list) failed for ${db}"
      ;;
    custom)
      artifact="${db}.dump"
      pg_dump -d "${db}" --format=custom --compress="${COMPRESS}" \
              --file="${DEST}/${artifact}" \
        || die "pg_dump (custom) failed for ${db}"
      pg_restore --list "${DEST}/${artifact}" >/dev/null \
        || die "verify (pg_restore --list) failed for ${db}"
      ;;
    *) die "unknown PG_DUMP_FORMAT='${PG_DUMP_FORMAT}' (use directory|custom)" ;;
  esac
  log "  -> ${artifact} ($(du -h "${DEST}/${artifact}" | cut -f1))"
done

# --- 3. Manifest + checksums --------------------------------------------------
{
  echo "backup_utc=${TS}"
  echo "server_version=$(psql -tAc 'SHOW server_version')"
  echo "format=${PG_DUMP_FORMAT}"
  echo "databases=${DATABASES}"
  echo "host=${PGHOST}:${PGPORT}"
} > "${DEST}/MANIFEST.txt"
( cd "${DEST}" && sha256sum ./* > SHA256SUMS 2>/dev/null || true )
log "wrote MANIFEST.txt + SHA256SUMS"

# --- 4. Offload to vStorage / S3 (optional) ----------------------------------
if [ -n "${S3_ENDPOINT}" ] && [ -n "${S3_BUCKET}" ]; then
  command -v aws >/dev/null || die "aws-cli not found but S3_ENDPOINT/S3_BUCKET set"
  # Non-AWS S3 (vStorage/MinIO) needs path-style addressing.
  aws configure set default.s3.addressing_style path 2>/dev/null || true
  s3uri="s3://${S3_BUCKET}/${S3_PREFIX}/${TS}/"
  log "offloading to ${s3uri} via ${S3_ENDPOINT} ..."
  aws --endpoint-url "${S3_ENDPOINT}" s3 cp "${DEST}/" "${s3uri}" --recursive \
    || die "S3 offload failed"
  log "offload complete"
else
  log "S3 offload skipped (set S3_ENDPOINT + S3_BUCKET to enable)"
fi

# --- 5. Local retention -------------------------------------------------------
log "pruning local backups older than ${RETENTION_DAYS} day(s)..."
find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" \
  -exec rm -rf {} + 2>/dev/null || true

log "DONE. Backup at ${DEST}"
