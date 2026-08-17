# Backup tooling — Customer360 Postgres

Runnable **logical** backup/restore for `customer360` + `db_keycloak` (PostGIS + pgvector),
with offload to **vStorage** (S3). This is the logical track from
[../02-backup-and-recovery.md](../02-backup-and-recovery.md); pair it with a **physical/PITR**
track (pgBackRest / CNPG / VNG Backup Center) for full DR.

## Files

| File | What it is |
| --- | --- |
| `pg_backup.sh` | Dump globals + each DB (directory/parallel, zstd), verify, checksum, offload to S3, prune. |
| `pg_restore.sh` | Restore globals then DBs, with pgvector index-rebuild tuning pre-set. |
| `Dockerfile.backup` | Runner image: PG16 client tools + aws-cli + zstd. |
| `backup-cronjob.yaml` | K8s `CronJob` (any topology: in-cluster StatefulSet or VNG vDB). |
| `docker-compose.backup.yml` | Compose backup sidecar overlay. |
| `backup-secret.example.env` | Secret template (PGPASSWORD + vStorage keys). **Never commit real values.** |

## Quick start

### A) Local one-shot (needs a `psql`/`pg_dump` 16 client on PATH)

```bash
export PGHOST=localhost PGPORT=5432 PGUSER=postgres PGPASSWORD=... \
       BACKUP_ROOT=./_backups
bash postgres/docs/backup/pg_backup.sh
# -> ./_backups/<UTC-timestamp>/{globals.sql, customer360.dir.tar, db_keycloak.dir.tar, MANIFEST.txt, SHA256SUMS}
```

### B) Docker Compose sidecar

```bash
docker compose \
  -f docker-compose.yml \
  -f postgres/docs/backup/docker-compose.backup.yml \
  up -d --build pg-backup
docker logs -f customer360-pg-backup      # watch the first run
```
Set `BACKUP_S3_ENDPOINT` / `BACKUP_S3_BUCKET` / `BACKUP_S3_ACCESS_KEY` / `BACKUP_S3_SECRET_KEY`
in the stack `.env` to enable offload; leave blank for local-only backups (in the
`customer360-pgbackups` volume).

### C) Kubernetes CronJob

```bash
# 1. build & push the runner image
docker build -f postgres/docs/backup/Dockerfile.backup \
  -t <registry>/customer360-pg-backup:16 postgres/docs/backup
docker push <registry>/customer360-pg-backup:16

# 2. create the Secret (fill backup-secret.example.env -> backup-secret.env first)
kubectl create secret generic postgres-backup-secrets \
  --from-env-file=postgres/docs/backup/backup-secret.env -n <namespace>

# 3. edit the image + S3 env in backup-cronjob.yaml, then apply
kubectl apply -f postgres/docs/backup/backup-cronjob.yaml -n <namespace>

# 4. test immediately without waiting for the schedule
kubectl create job --from=cronjob/postgres-logical-backup backup-now -n <namespace>
kubectl logs -f job/backup-now -n <namespace>
```

## Restore (and restore drill)

```bash
export PGHOST=<restore-target> PGUSER=postgres PGPASSWORD=...
# from a local backup dir:
bash postgres/docs/backup/pg_restore.sh ./_backups/<timestamp>
# or fetch from vStorage first:
export S3_ENDPOINT=https://hcm04.vstorage.vngcloud.vn
S3_SOURCE=s3://c360-prod-backups/postgres/<timestamp> \
  bash postgres/docs/backup/pg_restore.sh
```

> **The restore target MUST carry postgis + pgvector (≥ source version)** — use an image built
> from `../../Dockerfile` (`customer360-postgres:local`), **not** stock `postgis/postgis:16-3.5`.
> Logical restore **rebuilds** HNSW/IVFFlat indexes — tune with `RESTORE_MAINT_WORK_MEM` and
> `RESTORE_PARALLEL_MAINT_WORKERS` (see [../03-pgvector-backup.md](../03-pgvector-backup.md)).
> On Docker, the runner's `--shm-size` must be ≥ `RESTORE_MAINT_WORK_MEM` for parallel builds.

Full drill checklist: [../02-backup-and-recovery.md#restore-drill-runbook](../02-backup-and-recovery.md#restore-drill-runbook).

## Configuration (env vars)

| Var | Default | Notes |
| --- | --- | --- |
| `PGHOST` / `PGPORT` / `PGUSER` | `postgres` / `5432` / `postgres` | Connection. |
| `PGPASSWORD` | — (**required**) | From Secret / `.env`. |
| `DATABASES` | `customer360 db_keycloak` | Space-separated. |
| `PG_DUMP_FORMAT` | `directory` | `directory` (parallel) or `custom` (single file). |
| `JOBS` | `4` | Parallel workers (directory format). |
| `COMPRESS` | `zstd` | `zstd`/`lz4`/`gzip`/`none`. |
| `BACKUP_ROOT` | `/backups` | Local staging dir. |
| `RETENTION_DAYS` | `7` (`3` in CronJob) | Local pruning; S3 lifecycle owns offsite retention. |
| `S3_ENDPOINT` / `S3_BUCKET` / `S3_PREFIX` | — | Set both endpoint+bucket to enable offload. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` | — / — / `us-east-1` | vStorage keys; region required even if ignored. |
| `RESTORE_MAINT_WORK_MEM` | `2GB` | Restore only — pgvector index build. |
| `RESTORE_PARALLEL_MAINT_WORKERS` | `4` | Restore only — parallel index build. |
| `DROP_EXISTING` | `false` | Restore only — `true` recreates DBs (destructive). |

## Offsite retention & immutability (vStorage)

Local pruning (`RETENTION_DAYS`) only trims the staging copy. Enforce real retention + ransomware
protection **on the bucket**:

- **Lifecycle Expiration** rule → auto-delete objects older than N days (your GFS window).
- **Object Lock (WORM)** on `c360-prod-backups` → backups can't be deleted before expiry.
- **Versioning** (already enabled in prod Terraform) → recover overwritten objects.

Point any S3 client at vStorage with a custom endpoint + **path-style**:
```bash
aws configure set default.s3.addressing_style path
aws --endpoint-url https://hcm04.vstorage.vngcloud.vn s3 ls s3://c360-prod-backups/postgres/
```

## Scope & limits

- **Logical only.** No PITR here — add a physical track (pgBackRest / CNPG / VNG Backup Center)
  for point-in-time recovery and fast large-table restores. See [../02](../02-backup-and-recovery.md).
- **Per-tenant slices** (RLS `tenant_id`) need a custom `pg_dump --table`/`COPY … WHERE` filter —
  not automated here.
- The Compose sidecar's sleep-loop scheduler is deliberately minimal; **prefer the K8s CronJob**
  for production scheduling.
