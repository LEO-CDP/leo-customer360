# PostgreSQL — Scaling & Backup (Customer360 CDP)

Operational documentation for the Customer360 PostgreSQL tier: **how to scale it** and
**how to back it up and restore it** across the three ways this repo runs Postgres.

These docs are grounded in this repo's actual setup, not generic advice. If you change
the deployment, update these docs alongside it.

## What we run

| Fact | Value | Source in repo |
| --- | --- | --- |
| Engine | **PostgreSQL 16** | `postgres/Dockerfile` (`FROM postgis/postgis:16-3.5`) |
| Spatial | **PostGIS 3.5** | base image |
| Vectors | **pgvector** (`vector`, added via `postgresql-16-pgvector` apt) | `postgres/Dockerfile` |
| Other extensions | `uuid-ossp`, `pgcrypto`, `pg_trgm`, `fuzzystrmatch` | `postgres/init/00-extensions.sql` |
| Databases | **`customer360`** (app, multi-tenant) + **`db_keycloak`** (SSO) | `init/*.sql`, compose, k8s |
| Tenant isolation | **Row-Level Security on `tenant_id`** — one shared cluster | `database-init/database-schema.sql` |
| App role | **`customer360_app`** (non-superuser; RLS is inert as superuser) | `terraform/modules/db-bootstrap` |

## Three deployment modes

| Mode | Where | Manifest | HA? | Backup owner |
| --- | --- | --- | --- | --- |
| **Docker Compose** | single host / VM | `docker-compose.yml` (`postgres` service, `customer360-pgdata` volume) | No (single writer) | **You** — `backup/` tooling here |
| **Kubernetes (in-cluster)** | kind / dev / self-managed | `k8s/components/in-cluster-data/postgres.yaml` (StatefulSet, 1 replica, 5Gi PVC) | No (1 replica) | **You** — CronJob in `backup/`, or migrate to an operator |
| **VNG Cloud vDB** | production (VKS overlay) | `terraform/modules/postgres` → managed vDB; `k8s/overlays/vks` points the app at it | Yes (cluster topology, auto-failover) | **VNG Backup Center** + your logical dumps to vStorage |

> **VNG = VNG Cloud** (branded **GreenNode** in the newer console/docs). Managed database =
> **vDB**; S3-compatible object storage = **vStorage** (`https://hcm03.vstorage.vngcloud.vn`,
> `https://hcm04.vstorage.vngcloud.vn`); managed Kubernetes = **VKS**.

## Documents

1. **[01-scaling.md](01-scaling.md)** — Scaling best practices: config tuning for PG16,
   connection pooling, read replicas/replication, partitioning the high-volume event table,
   and the per-mode playbooks (Docker single-container, K8s operator vs StatefulSet, VNG vDB
   standalone→cluster).
2. **[02-backup-and-recovery.md](02-backup-and-recovery.md)** — Backup & recovery strategy:
   logical vs physical, PITR, the 3-2-1 rule, verification/restore drills, and per-mode
   procedures. Includes the **prod HA-cluster backup gap** and how to close it.
3. **[03-pgvector-backup.md](03-pgvector-backup.md)** — pgvector-specific backup/restore:
   why vector *data* dumps fine but vector *indexes* get rebuilt, the extension-version trap,
   and how to make restores fast.
4. **[04-pgbackrest-pitr.md](04-pgbackrest-pitr.md)** — the **physical/PITR track**: continuous
   WAL archiving + pgBackRest base backups to vStorage, with a PITR restore runbook. Docker +
   self-managed K8s only (vDB uses Backup Center).
5. **[backup/](backup/)** — **runnable** backup tooling. Logical track: `pg_backup.sh`,
   `pg_restore.sh`, a K8s `CronJob`, a Compose sidecar, a backup image
   ([backup/README.md](backup/README.md)). Physical track: `pgbackrest/` — config, a Compose
   overlay, and a K8s sidecar patch ([04](04-pgbackrest-pitr.md)).

## TL;DR recommendations

- **Scaling.** Keep a single shared cluster (RLS handles tenants). Scale **up** first (tune
  `shared_buffers`/`work_mem`/autovacuum/WAL), put a **pooler** (PgBouncer) in front so
  `max_connections` stays modest, then scale **out reads** with replicas. Partition the
  append-heavy events table by time. In K8s, **use an operator (CloudNativePG)** rather than
  the hand-rolled 1-replica StatefulSet for anything beyond dev.
- **Backups: run two independent tracks.**
  - **Physical + WAL / PITR** for low RPO/RTO disaster recovery (self-managed:
    **pgBackRest** to vStorage; K8s: **CloudNativePG + Barman Cloud plugin**; VNG vDB:
    **Backup Center** automatic + manual snapshots).
  - **Logical** (`pg_dump`) for portability, per-tenant/per-database extraction, and
    protection against physical corruption — that's what `backup/pg_backup.sh` gives you.
- **pgvector.** For large vector tables, prefer **physical** restore (indexes copied as-is).
  For logical restore, budget for **HNSW/IVFFlat index rebuild** and raise
  `maintenance_work_mem` + parallel workers. Always restore onto an image that carries
  `postgresql-16-pgvector` at a version **≥** the source.
- **A backup you have never restored is not a backup.** Schedule restore drills
  ([runbook in 02](02-backup-and-recovery.md#restore-drill-runbook)).

## The one gap to fix in prod

This repo's **prod Terraform uses `pg_topology = "cluster"`** (`terraform/environments/prod/main.tf`).
The `vngcloud_vdb_postgresql_cluster` resource **does not accept** `backup_auto` /
`backup_duration` / `backup_time` — those are **standalone-only**. So enabling `backup_auto`
in variables does nothing for the prod cluster. Prod backups must be configured through
**VNG Backup Center** (`backup_policy_id` / `backup_location_id` on the cluster resource) and
supplemented by the **logical dump CronJob** in `backup/`. See
[02-backup-and-recovery.md](02-backup-and-recovery.md#vng-cloud-vdb).
