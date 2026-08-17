# Scaling PostgreSQL 16 — Best Practices

How to scale the Customer360 Postgres tier. Read top-to-bottom for the reasoning; jump to
the [per-mode playbooks](#per-mode-playbooks) for the concrete knobs.

**Scaling order of operations** (cheapest/safest first):

1. **Tune the config** for the box you already have → [§1](#1-vertical-scaling--config-tuning)
2. **Add a connection pooler** so `max_connections` stays small → [§2](#2-connection-pooling)
3. **Partition** the append-heavy event table → [§3](#3-partitioning-the-high-volume-events-table)
4. **Scale reads** with replicas → [§4](#4-horizontal--read-scaling)
5. **Shard** (Citus) only if a single primary genuinely can't keep up → [§4](#when-to-shard-citus)

> **Topology decision for a multi-tenant CDP:** keep **one shared cluster**. Tenant isolation
> is enforced *inside* the database with **Row-Level Security on `tenant_id`**
> (`database-init/database-schema.sql`), so a single shared cluster is the correct topology —
> do **not** spin up a database per tenant. This makes "scaling" a question of sizing one
> cluster well, not orchestrating hundreds.

---

## 1. Vertical scaling & config tuning

Postgres defaults are tiny (they must boot on a laptop). The single highest-leverage scaling
action is setting these for the actual RAM/CPU/disk of the node. Numbers below use a
**reference node: 32 GB RAM, 8 vCPU, NVMe SSD** — scale the formulas to your box.

### Memory

| Parameter | PG16 default | Set to | 32 GB example | Why |
| --- | --- | --- | --- | --- |
| `shared_buffers` | `128MB` | ~25% RAM (cap ~40%) | **8 GB** | Postgres's own page cache. Past ~40% it just competes with the OS cache. |
| `effective_cache_size` | `4GB` | 50–75% RAM | **24 GB** | Planner hint only (allocates nothing). Higher → favors index scans. |
| `work_mem` | `4MB` | 16–64 MB | **32–64 MB** | **Per sort/hash node, per query.** The #1 OOM footgun — a parallel, multi-join query multiplies it. Size against the *pooled* backend count, not `max_connections`. |
| `maintenance_work_mem` | `64MB` | 512 MB–2 GB | **1 GB** | VACUUM, `CREATE INDEX`, `ALTER TABLE`. **Matters a lot for pgvector index builds** — see [03](03-pgvector-backup.md). |
| `huge_pages` | `try` | `on` once `shared_buffers` > ~8 GB | `on` | Fewer TLB misses. Reserve `vm.nr_hugepages` on the host to cover `shared_buffers`. |
| `wal_buffers` | `-1` (auto ≈16 MB) | leave `-1` | 16 MB | Auto is fine; pin 16–64 MB only on very write-heavy loads. |

### Planner cost & I/O (SSD / NVMe)

| Parameter | Default | SSD/NVMe | Why |
| --- | --- | --- | --- |
| `random_page_cost` | `4.0` | **1.1** | Random reads are nearly as cheap as sequential on SSD/NVMe. The default assumes spinning disks and suppresses index usage. |
| `effective_io_concurrency` | `1` | **100–300** | Prefetch depth for bitmap heap scans. The manual: for SSDs the best value "might be in the hundreds." |
| `default_statistics_target` | `100` | **250–500** on large/skewed tables | Better row estimates → better plans; raises `ANALYZE` cost. Prefer per-column `ALTER TABLE … SET STATISTICS`. |

### WAL & checkpoints (write throughput)

| Parameter | Default | High-write | Why |
| --- | --- | --- | --- |
| `checkpoint_timeout` | `5min` | **15min** (up to 30) | Fewer, fatter checkpoints → less full-page-image churn. |
| `max_wal_size` | `1GB` | **8–16 GB** | Size to hold ≥1 checkpoint interval of WAL. Goal: checkpoints fire on the **timer**, not because WAL filled. |
| `min_wal_size` | `80MB` | **2–4 GB** | Avoid recycle thrash during bursts. |
| `checkpoint_completion_target` | `0.9` | keep **0.9** | Spreads checkpoint I/O. **Never 1.0** (won't finish in time). |
| `wal_compression` | `off` | **`lz4`** | Shrinks WAL (full-page images), replication traffic, and backups. `lz4` ≈ pglz ratio at far less CPU; `zstd` smaller but heavier. |

Watch `log_checkpoints` (on by default since PG15): if checkpoints occur closer together than
`checkpoint_timeout`, raise `max_wal_size`.

### Autovacuum (critical for a CDP with high insert/update volume)

Defaults are far too lax for a billion-row table: `autovacuum_vacuum_scale_factor = 0.2` means
a table waits until **20% of rows are dead** before vacuuming (200M dead tuples on a 1B-row
table). Bloat → slow scans → more bloat.

- **Lower the scale factor globally to ~0.05**, and **per-table to 0.01–0.02** on the hottest
  tables. On very large tables, set `autovacuum_vacuum_scale_factor = 0` **plus** a fixed
  `autovacuum_vacuum_threshold` (e.g. 100k–1M) so vacuum triggers on an absolute row count.
- **Raise `autovacuum_vacuum_cost_limit` to 1000–2000** (from 200) so vacuum keeps up on fast
  storage. The budget is **shared across all workers**, so raise it when you raise workers.
- **`autovacuum_max_workers` 4–6** when you have many partitions/tables (needs restart).
- **`autovacuum_vacuum_insert_scale_factor` / `_threshold`** (PG13+) drive insert-triggered
  vacuums that keep the visibility map current → important for append-only `raw_events`
  (index-only scans, page-freezing).
- Apply aggressive values **per-table** (`ALTER TABLE … SET (autovacuum_… = …)`), not globally,
  so small tables keep sane behavior.

### `max_connections`

Keep **modest (100–200)** and put a pooler in front (§2). Each connection is an OS process with
its own memory; hundreds of idle backends waste RAM, add context-switch/lock contention, and
multiply your `work_mem` exposure.

> **Config quick-set:** apply these via
> `docker-compose.yml` `command: postgres -c key=value …`, a mounted `postgresql.conf`, a K8s
> ConfigMap, or (VNG vDB) a **config group** (`vngcloud_vdb_relational_config_group` /
> `..._cluster_config_group`, wired via `pg_config_values` in Terraform). Prod already sets
> `max_connections=200`, `autovacuum=true` in `terraform/environments/prod/main.tf` — extend
> that map with the values above.

---

## 2. Connection pooling

Postgres has **one process per connection**; useful concurrency is bounded by cores and disk,
not by connection count. The model: a **small** server-side `max_connections`, fronted by a
pooler that multiplexes many client connections onto few backends.

**PgBouncer** (the default choice — single-threaded C, extremely stable, ~20–40k txn/s/core):

- **`pool_mode = transaction`** for web/API workloads (backend returned at each COMMIT →
  maximum reuse). `session` mode only if you rely on session-scoped state (`LISTEN/NOTIFY`,
  session `SET`, session advisory locks).
- **Pool sizing:** backends should be a small multiple of cores — start
  `default_pool_size ≈ cores × 2..4`. Keep the **sum of all pools below the server
  `max_connections`**, leaving headroom for admin/maintenance. Raise `max_client_conn` (the
  client-facing cap) freely.
- **Prepared statements under transaction pooling:** works since **PgBouncer 1.21** for
  **protocol-level** prepared statements (JDBC/libpq/most drivers) via `max_prepared_statements`
  (default 200). SQL-level `PREPARE`/`EXECUTE` are **not** made pool-safe — use your driver's
  native prepared-statement API.

**When to use PgCat / Supavisor instead:** if you need a multithreaded pooler, built-in
read/write split, sharding, or failover-awareness in the pooler itself. PgBouncer scales by
running multiple CPU-pinned instances.

**Sidecar vs central pooler in K8s:** a **central** pooler (one `Deployment`+`Service`, ≥2
replicas) is a single place to enforce the backend cap and add read/write routing — prefer it,
and with CloudNativePG use its managed **`Pooler`** CRD rather than hand-rolling. A **sidecar**
per app has the lowest network hop but you must still ensure Σ(pools) < server `max_connections`.

---

## 3. Partitioning the high-volume events table

For a CDP the raw behavioral/event table (`cdp.raw-events` ingestion → an events table) grows
without bound and dominates VACUUM cost. **Declarative range partitioning by time** is the
first horizontal tool — it's single-node, so it needs no new infrastructure.

- **`PARTITION BY RANGE (event_time)`.** Monthly partitions for moderate volume, **daily** for
  heavy ingest. Each child + its indexes stay small; the planner **prunes** to the relevant
  partitions; retention becomes an O(1) `DETACH`/`DROP` instead of a giant `DELETE`.
- **Automate with `pg_partman`** (PG14+, uses native partitioning, no triggers):
  `create_parent()` to set up, `premake` (default 4) to keep future partitions ready,
  retention to auto-**detach or drop** old partitions, and the `pg_partman_bgw` background
  worker (in `shared_preload_libraries`) to run maintenance. Example: keep 180 days, then drop.
- **Index strategy for time-series:** a **BRIN index on `event_time`** is tiny and ideal for
  broad time-range scans over large/cold partitions; keep targeted **B-tree** indexes only on
  point-lookup columns (`tenant_id`, `profile_id`) and on hot/recent partitions.
- **Multi-tenant refinement:** if one tenant dominates, sub-partition by `hash (tenant_id)`
  under the time range, and always include `tenant_id` in queries so pruning (and, later, Citus
  shard-routing) stays effective.

---

## 4. Horizontal / read scaling

### Read replicas (streaming replication)

WAL streams primary → standby; standbys run `hot_standby = on` (default) and serve read-only
queries. Offload reporting, analytics, exports, and read-heavy tenants to replicas.

- **Async (default):** lowest commit latency; a primary crash can lose transactions not yet
  shipped (loss ∝ replication lag). The right default, and the only sane choice across regions.
- **Sync** (`synchronous_standby_names` + `synchronous_commit`): near-zero data loss, but
  commits **block** if the required standby is down. Use **quorum sync** (`ANY n (…)`) with ≥2
  candidates so one failure doesn't stall writes. Reserve for paths that truly can't lose data.
- **Routing:** Postgres has no built-in read/write split. Use an app-level read DSN, a splitting
  pooler (PgCat/Pgpool), or an operator's `-ro`/`-r` Services (CloudNativePG). Watch **replica
  lag** for read-after-write correctness; send must-be-fresh reads to the primary.

### Logical replication (not raw scaling, but essential)

Row/table-selective, decodes across **major versions**. Prime use: **near-zero-downtime major
upgrades** (run PG16 + PG17 side by side, replicate, cut over via the pooler). Caveats:
sequences and large objects are **not** replicated (advance sequences at cutover), and
**extensions + schema must pre-exist on the subscriber** — so PostGIS **and pgvector must be
installed on the subscriber before subscribing**.

### When to shard (Citus)

Only when write throughput or dataset size genuinely exceeds one primary. For multi-tenant,
distribute by **`tenant_id`** so tenant-scoped queries route to a single shard; Citus 12+ also
supports **schema-based sharding**. Until then, **partitioning + read replicas is simpler and
usually enough.**

---

## Per-mode playbooks

### Docker Compose (single host)

The `postgres` service in `docker-compose.yml` today runs with `cpus: "2"`, `memory: 1g`. That's
a *dev* footprint. For a real single-host deployment:

- **`replicas: 1` only.** Postgres is a single writer — two containers on one volume = corruption.
  Compose gives you **no HA**; for HA you need replication + failover (→ K8s operator or VNG vDB).
- **Set `shm_size: 512m`–`1g`.** Docker defaults `/dev/shm` to 64 MB, which breaks parallel query
  / large hashes / parallel index builds with `could not resize shared memory segment`. This is
  **separate from `shared_buffers`** and is required for pgvector parallel HNSW builds.
- **Memory:** raise the limit well above `shared_buffers`. Leave room for `work_mem × concurrency`,
  `maintenance_work_mem`, and the **OS page cache** (where `effective_cache_size` lives). Too
  tight a cgroup limit → the OOM killer reaps a backend → crash-recovery restart. Rule of thumb:
  `shared_buffers ≈ 25%` of the container limit.
- **Storage:** keep `PGDATA` on the **named volume** `customer360-pgdata` (already the case) — never
  on the overlay/ephemeral layer, never a bind mount for prod.
- **Apply tuning** via `command: postgres -c shared_buffers=… -c work_mem=… -c max_wal_size=… …`
  or a mounted `postgresql.conf`. Add a **PgBouncer** container in transaction mode.
- **Huge pages:** add capability `IPC_LOCK` + `ulimit memlock=-1`, reserve host hugepages, set
  `huge_pages=on`.

### Kubernetes

The in-cluster manifest (`k8s/components/in-cluster-data/postgres.yaml`) is a **1-replica
StatefulSet with a 5Gi PVC** — perfect for kind/dev, **not** production.

- **For anything beyond dev, use an operator — CloudNativePG (CNPG).** It's the 2025–2026 default
  (CNCF Sandbox since Jan 2025, community-governed). A bare StatefulSet gives you stable identity
  and storage but **nothing** for the database: no failover, no replica bootstrap, no
  backups/PITR, no controlled minor-version upgrades. CNPG gives all of that plus read Services
  for replica scaling and a managed `Pooler`. (See [02](02-backup-and-recovery.md) for its backup
  model.) It manages Pods+PVCs directly (not a StatefulSet) for finer failover control.
- **If you must hand-roll the StatefulSet:** provisioned-IOPS `storageClass` with
  `volumeBindingMode: WaitForFirstConsumer`; **pod anti-affinity** on `kubernetes.io/hostname`
  + topology spread across zones; a **PodDisruptionBudget** (`maxUnavailable: 1`); **memory
  `requests == limits`** (protect against OOM eviction) with CPU headroom (`request < limit`);
  `pg_isready` readiness. You still have to build replica bootstrap, failover, and WAL archiving
  yourself — which is the whole argument for the operator.
- **Raise the PVC** well above 5Gi and pick a fast class; size `shared_buffers`/`work_mem` against
  the pod's memory limit exactly as in the Docker section.

### VNG Cloud vDB (production)

The VKS overlay points the app at a **managed vDB PostgreSQL** provisioned by Terraform
(`terraform/modules/postgres`). Two topologies:

- **`standalone`** (`vngcloud_vdb_relational_database`) — single node, cheaper; dev/PoC. Has
  first-class backup args (`backup_auto`, `backup_duration`, `backup_time`).
- **`cluster`** (`vngcloud_vdb_postgresql_cluster`) — **2–10 nodes, 1 writer + N readers with
  automatic failover**, separate read-write and read-only endpoints. **This is what prod uses.**

Scaling on vDB:

- **Vertical:** change the `package_name` (instance class) and `volume_size`; apply PG params via a
  **config group** (`pg_config_values` in Terraform → `vngcloud_vdb_postgresql_cluster_config_group`).
  Note some params (e.g. `cleanup.policy` on Kafka; certain PG settings) are console/config-group
  only — verify what the config group accepts.
- **Read scaling:** the cluster exposes a **read-only endpoint** (`ro_host` / `ro_port` in
  `terraform/modules/postgres/outputs.tf`). Point read-heavy workers at it; the module's comment
  notes you can add `15432` security-group rules for the RO endpoint.
- **Connections:** vDB has its own `max_connections` ceiling per package — still front it with a
  PgBouncer `Pooler` in VKS.
- **Extensions:** PostGIS and pgvector are supported on vDB PostgreSQL (pgvector only on instances
  created **≥ 2024-08-01**; older instances need a support request). `fuzzystrmatch`, `pgcrypto`,
  `uuid-ossp` are enable-on-demand via `CREATE EXTENSION` — this is exactly what the db-bootstrap
  step does.

---

## Quick reference — starting config (adapt to node size)

```conf
# memory (reference: 32 GB / 8 vCPU node)
shared_buffers = 8GB
effective_cache_size = 24GB
work_mem = 48MB
maintenance_work_mem = 1GB
huge_pages = on

# planner / IO (SSD/NVMe)
random_page_cost = 1.1
effective_io_concurrency = 200
default_statistics_target = 200

# WAL / checkpoints (write-heavy)
wal_compression = lz4
checkpoint_timeout = 15min
max_wal_size = 16GB
min_wal_size = 4GB
checkpoint_completion_target = 0.9

# connections (front with PgBouncer)
max_connections = 200

# autovacuum (raise globally; tighten hottest tables per-table)
autovacuum_max_workers = 5
autovacuum_vacuum_cost_limit = 1500
autovacuum_vacuum_scale_factor = 0.05
autovacuum_analyze_scale_factor = 0.02
```

Per-table example for the events table:

```sql
ALTER TABLE events SET (
  autovacuum_vacuum_scale_factor = 0.01,
  autovacuum_vacuum_insert_scale_factor = 0.01,
  autovacuum_vacuum_cost_limit = 2000
);
```

---

### Sources

PostgreSQL 16 manual — [resource consumption](https://www.postgresql.org/docs/16/runtime-config-resource.html),
[query planning](https://www.postgresql.org/docs/16/runtime-config-query.html),
[WAL configuration](https://www.postgresql.org/docs/16/wal-configuration.html),
[autovacuum](https://www.postgresql.org/docs/16/runtime-config-autovacuum.html),
[replication](https://www.postgresql.org/docs/current/runtime-config-replication.html) ·
[PgBouncer config](https://www.pgbouncer.org/config.html) ·
[CloudNativePG](https://cloudnative-pg.io/) ·
Crunchy Data — [server performance](https://www.crunchydata.com/blog/optimize-postgresql-server-performance),
[high write loads](https://www.crunchydata.com/blog/tuning-your-postgres-database-for-high-write-loads),
[huge pages in containers](https://www.crunchydata.com/blog/huge-pages-and-postgres-in-containers) ·
[pg_partman docs](https://github.com/pgpartman/pg_partman/blob/master/doc/pg_partman.md) ·
Percona — [vacuum tuning](https://www.percona.com/blog/importance-of-postgresql-vacuum-tuning-and-custom-scheduled-vacuum-job/) ·
Instaclustr — [Docker & shared memory](https://www.instaclustr.com/blog/postgresql-docker-and-shared-memory/)
