# Backup & Recovery — Best Practices

How to back up and restore the Customer360 Postgres tier (`customer360` + `db_keycloak`,
with PostGIS + pgvector) across Docker, Kubernetes, and VNG Cloud vDB.

> pgvector has its own restore gotchas (index rebuild, extension-version matching). Those live
> in **[03-pgvector-backup.md](03-pgvector-backup.md)** — read it before your first restore drill.

---

## 0. Principles

**Run two independent tracks. Always.**

| Track | Tool(s) | Good for | Weak at |
| --- | --- | --- | --- |
| **Physical + WAL / PITR** | pgBackRest, WAL-G, `pg_basebackup`; CNPG Barman plugin; VNG Backup Center | Low RPO/RTO whole-cluster DR; **fast restore of large data & pgvector indexes (copied as-is)**; point-in-time recovery | Version/platform-locked; copies physical corruption verbatim |
| **Logical** (`pg_dump`) | `pg_dump` / `pg_dumpall` / `pg_restore` (see `backup/`) | Portability across versions/arch; **per-tenant / per-database** extraction; survives physical page corruption | Slow restore at scale (rebuilds indexes); snapshot-only (no PITR) |

They fail in **different ways** — physical corruption kills a physical backup but a logical dump
is still clean; a logical dump can silently miss something a byte-level base backup wouldn't.
Keep both.

**The 3-2-1 rule (hardened to 3-2-1-1-0):** 3 copies, on 2 media, 1 offsite, **1 immutable**,
**0 verified errors**. Attackers target backup repositories first — put the offsite copy behind
**Object Lock / WORM** (vStorage supports it) so a compromised credential can't wipe it.

**Retention — GFS (Grandfather-Father-Son):** daily (son) / weekly (father) / monthly
(grandfather), extended to yearly for compliance. Map onto pgBackRest `repo-retention-*` or a
vStorage **Lifecycle Expiration** rule.

**A backup you have never restored is not a backup.** → [Restore-drill runbook](#restore-drill-runbook).

**RPO / RTO:**
- **RPO** (max data loss) is bounded by how fast WAL leaves the box: streaming
  (`pg_receivewal` / sync) → seconds/zero; `archive_command` + `archive_timeout=60s` → ~1 min;
  daily logical dump → up to 24 h.
- **RTO** (time to restore) is dominated by data volume + **index rebuild** (huge for pgvector
  logical restores). Physical + delta restore is far faster than a logical reload.

---

## 1. Logical backup — `pg_dump` done right

This is what **[`backup/pg_backup.sh`](backup/pg_backup.sh)** automates. The essentials:

- **Dump globals first.** `pg_dump` does **not** dump roles. Run
  `pg_dumpall --globals-only > globals.sql` once, restore it with `psql` **before** the databases,
  so ownership (`postgres`, `customer360_app`) resolves.
- **Dump each database** with **directory format + parallelism**:
  `pg_dump -Fd -j 4 -Z zstd -f dump.d customer360`. Directory format is the **only** format that
  dumps in parallel (`-j`), and it restores in parallel too. Custom format (`-Fc`) is a
  convenient single file with parallel *restore* but serial dump — fine for small DBs.
- **Consistency:** `pg_dump` uses a **single transaction snapshot** for the whole run — it's
  consistent even under concurrent writes and blocks nobody. Parallel dump uses **synchronized
  snapshots** so all workers agree.
- **Restore portability:** set `--no-owner --no-privileges` **on `pg_restore`** (for archive
  formats these are ignored at dump time). Add `--clean --if-exists` to replace, `-C` to create
  the DB.
- **Extensions:** the dump emits `CREATE EXTENSION vector` / `postgis` / … but **not** the
  extension's code. **The restore target must already have those extension binaries installed**
  (i.e. an image built from `postgres/Dockerfile`, not stock `postgis/postgis:16-3.5`). See [03](03-pgvector-backup.md).
- **Multi-tenant extraction:** because everything is one cluster with RLS, a logical dump of
  `customer360` is a whole-tenant-set dump. To pull a **single tenant**, dump with a
  `--table`/row filter or `COPY (SELECT … WHERE tenant_id = …)` — a logical dump is the only way
  to slice by tenant.

Restore is automated by **[`backup/pg_restore.sh`](backup/pg_restore.sh)**.

---

## 2. Physical backup + WAL archiving (PITR)

For low-RPO DR on **self-managed** Postgres (Docker/VM), use **pgBackRest** (recommended) or
WAL-G. Barman is the server-centric alternative and the easiest route to RPO≈0 via
`pg_receivewal`.

> **This track is fully built out** in **[04-pgbackrest-pitr.md](04-pgbackrest-pitr.md)** with
> ready-to-run tooling in [`backup/pgbackrest/`](backup/pgbackrest/) (config + Compose overlay +
> K8s sidecar) and a PITR restore runbook. The config below is the summary; use doc 04 to deploy.

**Enable WAL archiving** (`postgresql.conf`):

```conf
wal_level = replica
archive_mode = on
archive_command = 'pgbackrest --stanza=c360 archive-push %p'
archive_timeout = 60s          # bound RPO during idle periods
```

**pgBackRest against vStorage (S3-compatible)** — `pgbackrest.conf`:

```ini
[global]
repo1-type=s3
repo1-s3-endpoint=hcm04.vstorage.vngcloud.vn   # region = host; use your zone's endpoint
repo1-s3-uri-style=path                        # REQUIRED for non-AWS S3 (MinIO/vStorage)
repo1-s3-region=us-east-1                       # required even if ignored by the provider
repo1-s3-bucket=c360-prod-backups               # the bucket Terraform pre-provisions
repo1-s3-key=<vstorage-access-key>
repo1-s3-key-secret=<vstorage-secret-key>
repo1-path=/pgbackrest
repo1-cipher-type=aes-256-cbc                   # client-side encryption at rest
repo1-cipher-pass=<passphrase>
repo1-bundle=y                                  # prerequisite for block incremental
repo1-block=y                                   # block incremental (pgBackRest >= 2.52.1)
repo1-retention-full-type=time
repo1-retention-full=14                         # keep 14 days of full backups

[c360]
pg1-path=/var/lib/postgresql/data/pgdata
```

```bash
pgbackrest --stanza=c360 stanza-create
pgbackrest --stanza=c360 backup --type=full     # then --type=diff / --type=incr on a schedule
pgbackrest --stanza=c360 --delta restore        # restore only changed files → low RTO
```

**PITR restore (native low-level):** restore the base backup, add `restore_command`, create an
empty `recovery.signal`, set `recovery_target_time = '2026-08-16 09:30:00+07'`, start Postgres;
it replays WAL to the target and forks a new timeline. Keep `.history` files forever.

> **pgvector + physical:** base backups copy the HNSW/IVFFlat index files **as-is** — **no
> rebuild on restore**. This is the big reason to keep a physical track for large vector tables.
> The restore host still needs the pgvector binary present. See [03](03-pgvector-backup.md).

---

## Per-mode procedures

### Docker Compose

**Logical (do this today):** run the sidecar in
[`backup/docker-compose.backup.yml`](backup/docker-compose.backup.yml), which cron-runs
[`pg_backup.sh`](backup/pg_backup.sh) against the `postgres` service and offloads to vStorage.
Or one-shot:

```bash
docker exec -e PGPASSWORD="$DB_PASSWORD" customer360-postgres \
  pg_dump -U postgres -Fc customer360 > customer360-$(date +%F).dump
```

- **Pitfall — cron `docker exec` fails silently** if the container is renamed/down. Always
  capture stdout/stderr **and check the exit code**; alert on non-zero. `pg_backup.sh` does this.
- **Pitfall — never `docker cp` the live `PGDATA`.** A copy of a running data dir is mid-write and
  **not crash-consistent**. Use `pg_dump` (consistent under load), or stop the container for a
  cold copy, or run pgBackRest for a proper physical backup.

**Physical/PITR:** run pgBackRest in the backup sidecar (image in
[`backup/Dockerfile.backup`](backup/Dockerfile.backup) carries it) with the config above, and set
`archive_command` on the `postgres` service.

**Restore into a fresh container** (also your drill):

```bash
docker run --rm -d --name pg_restore_test customer360-postgres:local  # MUST carry pgvector+postgis
docker exec -i pg_restore_test psql -U postgres < globals.sql
docker exec -i pg_restore_test pg_restore -U postgres -d customer360 --create \
  --no-owner --no-privileges -j 4 < customer360-2026-08-16.dump
```

### Kubernetes

**Recommended: CloudNativePG.** If you migrate off the hand-rolled StatefulSet (see
[01](01-scaling.md#kubernetes)), CNPG gives you native backups:

- **`ObjectStore` CRD** → the vStorage target (`endpointURL`, credentials via a `Secret`,
  `retentionPolicy`). WAL + base backups land there (Barman Cloud).
- Reference it from the `Cluster` under `.spec.plugins`
  (`name: barman-cloud.cloudnative-pg.io`, `isWALArchiver: true`).
- **`ScheduledBackup`** (cron) + on-demand **`Backup`** resources.
- **PITR restore = bootstrap a NEW cluster** from the recovery `ObjectStore` with a
  `recoveryTarget` (`targetTime`) — you don't restore in place.
- The in-tree `barmanObjectStore` field is **deprecated (CNPG 1.26, removed ~1.30)** — use the
  **plugin**.

> **In-tree StatefulSet users:** you have no operator backup. Use the **logical CronJob** below
> as your primary backup until you adopt CNPG.

**Logical CronJob (works with ANY topology, incl. the current StatefulSet):**
[`backup/backup-cronjob.yaml`](backup/backup-cronjob.yaml) runs `pg_backup.sh` from the
[backup image](backup/Dockerfile.backup) on a schedule and pushes dumps to vStorage.
Credentials come from a `Secret` ([`backup/backup-secret.example.env`](backup/backup-secret.example.env)).

**VolumeSnapshot (CSI) backups** are an option if your storage class supports them, but a raw
CSI snapshot is only **storage-crash-consistent** — for application consistency you must quiesce
/`fsfreeze` (CNPG's standby-based `snapshot` does this) and you still need the WAL archive to
restore an online snapshot. Prefer CNPG-orchestrated snapshots over hand-rolled ones.

### VNG Cloud vDB

> **This is the important one for prod, and it has a gap.** Prod runs
> `pg_topology = "cluster"` (`terraform/environments/prod/main.tf`).

**The gap:** the `vngcloud_vdb_postgresql_cluster` resource **does not accept** `backup_auto` /
`backup_duration` / `backup_time`. Those exist **only** on the standalone
`vngcloud_vdb_relational_database`. So the module's `backup_auto`/`backup_duration`/`backup_time`
variables (`terraform/modules/postgres/variables.tf`) have **no effect on a prod cluster**.

**What the cluster resource *does* expose** (verify current arg names against the
[vngcloud provider docs](https://registry.terraform.io/providers/vngcloud/vngcloud/latest/docs)):
- `backup_policy_id` — a **Backup Policy** (schedule + retention) defined in **VNG Backup Center**.
- `backup_location_id` — the **Backup Location** (where backups are stored).
- `backup_id` — a restore point to **create/restore the cluster from**.

**Action items to close the gap:**

1. **Create a Backup Policy + Location** in the VNG Backup Center console (or via Terraform if the
   provider exposes those resources), then wire `backup_policy_id` + `backup_location_id` into the
   prod cluster in `terraform/modules/postgres/main.tf`. **Verify the retention range for cluster
   policies in the console** (the 2–14 day range is the *standalone* limit).
2. **Take manual "Full Snapshot" backups** before risky changes. Unlike auto backups, **manual
   cluster backups survive cluster deletion** — use them as pre-migration safety nets.
3. **Add the logical CronJob** (`backup/backup-cronjob.yaml`) in VKS as a second, portable track —
   dumps land in vStorage and are restorable anywhere (incl. off VNG). This is also your
   protection against the fact that **vDB PITR is not documented** (snapshots only — verify with
   VNG support).

**vDB facts to keep in mind:**
- **Backups:** Full + Incremental (standalone) / Full Snapshot (cluster); **auto backups are
  deleted with the instance/cluster**, manual ones are retained. **Restore = create a NEW
  instance/cluster** from a restore point (new storage size ≥ backup size). Cross-zone/region
  backup and PITR are **not documented — verify in console.**
- **No physical restore into the managed service** — you can't `pgBackRest restore` into vDB. For
  migrations *into* vDB it's **logical dump/restore only**, so budget for pgvector index rebuild
  and confirm the vDB **pgvector version ≥** your source (halfvec/sparsevec need ≥ 0.7.0).
- **vStorage** is your offsite/immutable target: point `aws-cli`/`mc`/`rclone` at
  `--endpoint-url https://hcm04.vstorage.vngcloud.vn` (path-style), enable **Object Lock** on the
  backup bucket, and set a **Lifecycle Expiration** rule for retention. The `c360-<env>-backups`
  bucket is already pre-provisioned (`terraform/environments/prod/main.tf`,
  `vstorage_shared_buckets = ["events","exports","backups"]`, versioned).

---

## Restore-drill runbook

Untested backups rot silently. Run this **monthly** (and after any schema/extension change) and
treat a failure as Sev-1.

1. **Provision a throwaway target** carrying the SAME extensions: `customer360-postgres:local`
   (Docker), a scratch namespace (K8s), or a temporary vDB restored from a restore point (VNG).
2. **Restore globals, then databases** — see per-mode restore commands above, or
   [`backup/pg_restore.sh`](backup/pg_restore.sh).
3. **Prove the extensions loaded:**
   ```sql
   SELECT extname, extversion FROM pg_extension ORDER BY 1;   -- expect vector, postgis, pg_trgm, ...
   ```
4. **Prove the data is usable** (not just present):
   ```sql
   SELECT count(*) FROM <a core table>;                        -- row count sanity
   SELECT * FROM <geo table> WHERE ST_DWithin(geom, ...) LIMIT 1;   -- PostGIS works
   SELECT id FROM <vector table> ORDER BY embedding <-> '[...]' LIMIT 5;  -- pgvector index works
   ```
5. **For PITR:** recover to a chosen timestamp and confirm a known row's state at that time.
6. **Record** the RTO you observed, the backup age (→ actual RPO), and any errors. If the drill
   failed, the backup config is broken **now** — fix before you need it.
7. **Verify without a full restore too:** `pg_verifybackup` (checksums a `pg_basebackup` against
   its manifest) and `pgbackrest verify` (repo integrity) are cheap and catch structural damage,
   but they do **not** prove logical recoverability — the drill does.

---

### Sources

PostgreSQL 16 — [SQL Dump](https://www.postgresql.org/docs/16/backup-dump.html),
[pg_dump](https://www.postgresql.org/docs/16/app-pgdump.html),
[Continuous Archiving & PITR](https://www.postgresql.org/docs/16/continuous-archiving.html),
[pg_verifybackup](https://www.postgresql.org/docs/16/app-pgverifybackup.html) ·
[pgBackRest](https://pgbackrest.org/) ([config](https://pgbackrest.org/configuration.html),
[S3 guide](https://bun.uptrace.dev/postgres/pgbackrest-s3-backups.html)) ·
[WAL-G](https://wal-g.readthedocs.io/PostgreSQL/) ·
CloudNativePG — [backup](https://github.com/cloudnative-pg/cloudnative-pg/blob/main/docs/src/backup.md),
[Barman Cloud plugin](https://cloudnative-pg.io/plugin-barman-cloud/docs/concepts/),
[recovery](https://cloudnative-pg.io/docs/1.27/recovery/) ·
VNG / GreenNode — [vDB PostgreSQL cluster](https://docs.greennode.ai/vdb/relational-database-service-rds/postgresql-cluster),
[RDS backup](https://docs.greennode.ai/vdb/relational-database-service-rds/working-with-rds/sao-luu-du-lieu-cua-rds-instance),
[vStorage + aws-cli](https://docs.vngcloud.vn/vng-cloud-document/vstorage/object-storage/vstorage-hcm03/3rd-party-softwares/aws-cli/integrating-aws-cli-with-vstorage) ·
[vngcloud Terraform provider](https://registry.terraform.io/providers/vngcloud/vngcloud/latest/docs)
