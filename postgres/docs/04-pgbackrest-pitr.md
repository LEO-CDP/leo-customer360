# Physical Backup & PITR — pgBackRest

The **physical/PITR track**: continuous WAL archiving + periodic base backups to vStorage with
**pgBackRest**, giving low-RPO point-in-time recovery and fast restores of large data
(including pgvector indexes, copied byte-for-byte — no rebuild). This complements the **logical**
track (`../backup/pg_backup.sh`) from [02-backup-and-recovery.md](02-backup-and-recovery.md) —
run **both**.

> **Where this applies.** pgBackRest reads `PGDATA` and issues `pg_backup_start` on the DB host,
> so it works on the **Docker Compose** and **self-managed K8s StatefulSet** deployments — where
> you own the filesystem and can install pgbackrest into the DB image. It does **not** apply to
> **VNG Cloud vDB** (managed — no filesystem/superuser access); there, backups are **VNG Backup
> Center** (wired in the Terraform prod module) + the logical `pg_dump` CronJob. In K8s, if you
> adopt **CloudNativePG**, prefer its Barman Cloud plugin over hand-rolling pgBackRest — see
> [02](02-backup-and-recovery.md#kubernetes).

Tooling for this track: [`../backup/pgbackrest/`](../backup/pgbackrest/).

---

## Why pgBackRest

- **Block incremental** (`repo-block`, pgBackRest ≥ 2.52.1) — only changed blocks are stored, so
  diff/incr backups are small and fast.
- **`--delta` restore** — restores only files that differ from the target dir → low RTO.
- **Native S3 repo** against non-AWS endpoints (vStorage), **AES-256 client-side encryption**,
  parallelism (`process-max`), and **GFS retention** built in.
- **RPO** bounded by WAL shipping: `archive-async` + `archive_timeout=60s` → ~1 min; near-zero
  with synchronous streaming. **RTO** dominated by data volume, cut hard by delta restore.

---

## Architecture (this repo)

```
 ┌─────────────────────────┐         WAL segments (continuous)
 │ postgres (PGDATA owner)  │  archive_command = pgbackrest archive-push %p
 │  + pgbackrest binary     │ ───────────────────────────────────────────┐
 └──────────┬──────────────┘                                             │
            │ shares PGDATA volume + unix socket                         ▼
 ┌──────────▼──────────────┐   scheduled base backups        ┌───────────────────────┐
 │ pgbackrest scheduler     │  pgbackrest backup --type=... │  vStorage S3 repo       │
 │ (same image, sidecar)    │ ─────────────────────────────▶│  c360-*-backups/        │
 └─────────────────────────┘   + expire (retention)         │   pgbackrest/ (encrypted)│
                                                             └───────────────────────┘
```

- **`archive-push`** runs *inside* the postgres container (the server calls `archive_command`), so
  the **DB image must contain the pgbackrest binary** — added to `postgres/Dockerfile`.
- **`backup`/`expire`** run from a **scheduler** that shares the `PGDATA` volume and the postgres
  **unix socket** (so pgBackRest can read data files and open its control connection locally).
- The repo lives in the pre-provisioned **`c360-<env>-backups`** vStorage bucket, encrypted.

---

## Configuration

[`../backup/pgbackrest/pgbackrest.conf`](../backup/pgbackrest/pgbackrest.conf) holds the
non-secret config. **Secrets are injected via environment variables**, never committed:

| Env var | Maps to | What |
| --- | --- | --- |
| `PGBACKREST_REPO1_S3_KEY` | `repo1-s3-key` | vStorage access key |
| `PGBACKREST_REPO1_S3_KEY_SECRET` | `repo1-s3-key-secret` | vStorage secret key |
| `PGBACKREST_REPO1_CIPHER_PASS` | `repo1-cipher-pass` | repo encryption passphrase (keep it safe — **lose it, lose the backups**) |

Any pgBackRest option can be set via `PGBACKREST_<SECTION-less-OPTION-uppercased-with-underscores>`.

Key non-secret settings (see the file for the full set):

```ini
repo1-type=s3
repo1-s3-endpoint=hcm04.vstorage.vngcloud.vn   # your zone's endpoint
repo1-s3-uri-style=path                         # REQUIRED for non-AWS S3
repo1-s3-region=us-east-1                        # required even if ignored
repo1-s3-bucket=c360-prod-backups
repo1-cipher-type=aes-256-cbc
repo1-bundle=y                                   # prerequisite for block incremental
repo1-block=y                                    # block incremental (>= 2.52.1)
repo1-retention-full-type=time
repo1-retention-full=14                          # GFS: 14 days of fulls
repo1-retention-diff=4
compress-type=zst
process-max=4
[c360]
pg1-path=/var/lib/postgresql/data                # MUST equal PGDATA (see note)
pg1-socket-path=/var/run/postgresql
```

> **`pg1-path` must equal `PGDATA`.** Docker Compose default = `/var/lib/postgresql/data`; the K8s
> StatefulSet sets `PGDATA=/var/lib/postgresql/data/pgdata` — use that path there.

---

## Docker Compose — quick start

```bash
# 1. Build the DB image (now includes pgbackrest) + bring up the stack with the overlay
docker compose \
  -f docker-compose.yml \
  -f postgres/docs/backup/pgbackrest/docker-compose.pgbackrest.yml \
  up -d --build postgres pgbackrest

# 2. First-time: create the stanza + verify the repo/archiving are healthy
docker exec customer360-pgbackrest pgbackrest --stanza=c360 stanza-create
docker exec customer360-pgbackrest pgbackrest --stanza=c360 check

# 3. Take a full backup now (scheduler also runs it daily)
docker exec customer360-pgbackrest pgbackrest --stanza=c360 --type=full backup

# 4. Inspect
docker exec customer360-pgbackrest pgbackrest --stanza=c360 info
```

Set `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`, `PGBACKREST_CIPHER_PASS` (and optionally
`BACKUP_S3_ENDPOINT`/`BACKUP_S3_BUCKET`) in the stack `.env`. The overlay adds
`wal_level=replica`, `archive_mode=on`, and the `archive_command` to the `postgres` service.

> `archive_mode` change needs a Postgres **restart** (the overlay restarts the container). If
> `pgbackrest check` fails on archiving, confirm `archive_command` is set and the stanza exists.

---

## Kubernetes (self-managed StatefulSet) — sidecar

[`../backup/pgbackrest/pgbackrest-sidecar.k8s.yaml`](../backup/pgbackrest/pgbackrest-sidecar.k8s.yaml)
patches `k8s/components/in-cluster-data/postgres.yaml` to:

- add the `archive_command` + `archive_mode=on` args to the postgres container,
- add a **`pgbackrest` sidecar** (same image) sharing the `data` PVC and a socket `emptyDir`,
  running a `stanza-create` + scheduled `backup`/`expire` loop,
- mount `pgbackrest.conf` from a ConfigMap and secrets from a Secret.

Apply as a Kustomize patch or `kubectl apply` after adjusting `pg1-path` to
`/var/lib/postgresql/data/pgdata`. **Prefer CloudNativePG** if you can adopt it — it manages all
of this (and failover) declaratively.

---

## PITR restore runbook

Restore to a **new/empty** data dir, then recover to a target time.

```bash
# 0. Stop Postgres. NEVER restore over a running cluster.
docker compose stop postgres            # (K8s: scale the StatefulSet to 0)

# 1. Restore the base backup + WAL to a chosen point in time, into an EMPTY PGDATA.
#    --delta restores only differing files (fast). Pick ONE recovery target.
docker exec customer360-pgbackrest pgbackrest --stanza=c360 \
  --type=time --target="2026-08-16 09:30:00+07" --delta restore
#   other targets: --type=lsn --target=... | --type=xid --target=... | --type=immediate

# 2. Start Postgres. It replays WAL to the target, then (default) PAUSES at the target so you
#    can inspect before committing. pgBackRest writes recovery settings + recovery.signal.
docker compose start postgres

# 3. Verify you're at the right point, then promote to end recovery:
docker exec customer360-postgres psql -U postgres -c "SELECT pg_is_in_recovery();"
docker exec customer360-postgres psql -U postgres -c "SELECT pg_wal_replay_resume();"  # or set recovery_target_action=promote
```

- Recovery forks a **new timeline**; pgBackRest handles `.history` files. Keep them.
- **Full-cluster restore only** — physical restore is whole-cluster, not per-database/table. For a
  single tenant/table use the **logical** track.
- **pgvector/PostGIS:** the restore host image must carry the same extensions (it does — same
  `customer360-postgres:local`); vector indexes come back as files, **not rebuilt**. See
  [03-pgvector-backup.md](03-pgvector-backup.md).
- **Verify (don't trust):** `pgbackrest --stanza=c360 verify` checks repo integrity cheaply; a
  full restore drill into a throwaway target is the real test —
  [02 runbook](02-backup-and-recovery.md#restore-drill-runbook).

---

## Retention & immutability

- pgBackRest `repo1-retention-full`/`-diff`/`-archive` enforce GFS retention in the repo (`expire`
  runs after each backup).
- Layer vStorage **Object Lock (WORM)** + **Lifecycle** on `c360-prod-backups` for ransomware
  resistance (a compromised key can't wipe locked objects) — see
  [../backup/README.md](../backup/README.md#offsite-retention--immutability-vstorage).

---

### Sources

[pgBackRest](https://pgbackrest.org/) — [config reference](https://pgbackrest.org/configuration.html),
[user guide](https://pgbackrest.org/user-guide.html),
[S3 backups](https://bun.uptrace.dev/postgres/pgbackrest-s3-backups.html),
[block incremental (Crunchy)](https://www.crunchydata.com/blog/pgbackrest-file-bundling-and-block-incremental-backup) ·
PostgreSQL 16 — [Continuous Archiving & PITR](https://www.postgresql.org/docs/16/continuous-archiving.html)
