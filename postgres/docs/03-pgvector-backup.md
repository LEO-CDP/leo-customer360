# pgvector — Backup & Restore Specifics

pgvector needs no special *backup* handling, but it has three real *restore* traps. Read this
before your first restore drill.

> **One-line summary:** vector **data** dumps and restores like any other column, but vector
> **indexes** (HNSW/IVFFlat) are **rebuilt** during a logical restore — which can dominate restore
> time — while a **physical** backup copies them as-is. Either way, the restore target must carry
> the `postgresql-16-pgvector` binaries at a version **≥** the source.

In this repo, `vector` is installed by `postgres/Dockerfile`
(`apt-get install postgresql-16-pgvector`) on top of `postgis/postgis:16-3.5`, and enabled by
`postgres/init/00-extensions.sql` (`CREATE EXTENSION IF NOT EXISTS vector;`).

---

## Trap 1 — the extension-version match

- `pg_dump` emits `CREATE EXTENSION IF NOT EXISTS vector` **with no version pin** (by design, so a
  dump can load into a newer server). It does **not** dump the extension's C code.
- **The restore target must already have the pgvector binaries installed**, or `CREATE EXTENSION
  vector` fails with `could not open extension control file ".../vector.control"` and the **whole
  restore aborts**. (Note: the SQL name is `vector`, not `pgvector`.)
- **The target pgvector version must be ≥ the source.** The `halfvec` and `sparsevec` types
  arrived in **pgvector 0.7.0**; a dump using them will not restore onto an older pgvector.
- **Concrete rule for this project:** always restore onto an image **built from
  `postgres/Dockerfile`** (or another image that carries `postgresql-16-pgvector`), **never** stock
  `postgis/postgis:16-3.5` (it does **not** bundle pgvector). This applies to logical restore,
  physical restore, replicas, and logical-replication subscribers alike.
- Upgrading in place after installing newer binaries: `ALTER EXTENSION vector UPDATE;` (per
  database). If it can't find the new version, the new `.control`/`.sql` files aren't on the
  server's extension path yet.

**Check versions before restoring:**

```sql
-- on SOURCE and TARGET; TARGET must be >= SOURCE
SELECT extversion FROM pg_extension WHERE extname = 'vector';
SELECT default_version, installed_version FROM pg_available_extensions WHERE name = 'vector';
```

---

## Trap 2 — indexes are rebuilt on logical restore (the RTO killer)

`pg_dump` does **not** dump index contents — it emits the `CREATE INDEX … USING hnsw/ivfflat (…)`
DDL, and the index is **rebuilt from scratch** on restore. For HNSW on a large table this rebuild
can take **far longer than loading the data itself**.

**What drives the rebuild cost — and how to cut it:**

- **`maintenance_work_mem`** — "Indexes build significantly faster when the graph fits into
  `maintenance_work_mem`." Raise it big for the build window:
  ```sql
  SET maintenance_work_mem = '8GB';   -- session-level, for the restore/build
  ```
- **`max_parallel_maintenance_workers`** — parallel HNSW builds (pgvector ≥ 0.6.0) can be
  ~orders of magnitude faster. Raise for the build:
  ```sql
  SET max_parallel_maintenance_workers = 7;   -- plus the leader
  ```
  **Container caveat:** `--shm-size` (Docker) / `shm_size` (Compose) / `/dev/shm` sizing must be
  **≥ `maintenance_work_mem`** or the parallel HNSW build errors out. See
  [01-scaling.md](01-scaling.md#docker-compose-single-host).
- **Index parameters** raise build cost directly: HNSW `m` & `ef_construction`, IVFFlat `lists`.
- **IVFFlat ordering gotcha:** IVFFlat clusters *existing* rows at build time, so it must be built
  **after** the data is loaded. `pg_dump`'s natural order (data → then indexes) is correct — but if
  you hand-script "create index, then load," an IVFFlat built on an empty table gives poor recall.
  HNSW has no such data dependency.

**Best practice for a large logical restore:**

1. Restore **schema + data first, indexes last** (this is `pg_dump`'s default order; keep it).
2. Raise `maintenance_work_mem` + `max_parallel_maintenance_workers` for the build window.
3. Use `pg_restore -j N` — parallel restore builds different indexes concurrently.
4. Optionally split: `pg_restore --section=pre-data` + `--section=data`, then build the vector
   indexes yourself with `CREATE INDEX CONCURRENTLY` (non-blocking) tuned as above. For staged
   builds, remember to build **IVFFlat after** data load.

`backup/pg_restore.sh` sets the two build knobs by default (`RESTORE_MAINT_WORK_MEM`,
`RESTORE_PARALLEL_MAINT_WORKERS`).

---

## Trap 3 — text-representation blow-up in logical dumps

`pg_dump` writes each vector as its full decimal **text** form. A 1536-dim `float32` vector is
~6 KB on disk (`4*dim + 8` bytes) but can serialize to **15–20 KB+ of text**, so dump files for
large embedding tables inflate a lot and dump/restore becomes CPU-bound on float↔text conversion.

Mitigations:

- Use **`-Fc`/`-Fd` with compression** (`-Z zstd`) — the text compresses well. (`pg_backup.sh`
  defaults to directory + zstd.)
- Use **`-j`** for parallel dump (directory format) and parallel restore.
- Consider **`halfvec`** (2 bytes/dim instead of 4) for embeddings that tolerate reduced
  precision — halves both storage and dump width. (Requires pgvector ≥ 0.7.0 everywhere.)
- For very large vector tables, prefer the **physical track** (below) — no text conversion, no
  rebuild.

---

## Physical backups & PITR — pgvector is transparent

Because pgvector is "just files on disk":

- **Base backups (`pg_basebackup`, pgBackRest) copy the HNSW/IVFFlat index files directly — no
  rebuild on restore.** This is the single biggest reason to keep a physical track when your
  vector tables/indexes are large.
- **WAL / PITR works transparently** — the pgvector README notes it uses the WAL, so replication
  and point-in-time recovery behave exactly as for any Postgres data.
- **Caveats:** physical backups are locked to the **same PG major version and platform/arch**, and
  the pgvector **binary must exist on the restore host / standby** (same as Trap 1). A standby
  replaying a `CREATE EXTENSION vector` needs `vector.so` present.

---

## Decision guide

| Situation | Prefer |
| --- | --- |
| Large vector tables/indexes, whole-cluster DR, low RTO | **Physical** (pgBackRest / base backup + WAL) — indexes copied as-is |
| Cross-major-version move, per-tenant extraction, portability | **Logical** (`pg_dump`) — budget for index rebuild + tune build knobs |
| Migrating **into** VNG vDB (managed) | **Logical only** (no physical restore into vDB); confirm vDB pgvector version ≥ source |
| Point-in-time recovery | **Physical + WAL** (self-managed / CNPG). vDB PITR is undocumented — verify. |

---

### Sources

[pgvector README](https://github.com/pgvector/pgvector/blob/master/README.md) &
[CHANGELOG](https://github.com/pgvector/pgvector/blob/master/CHANGELOG.md) ·
[Supabase — pgvector 0.7.0 (halfvec/sparsevec)](https://supabase.com/blog/pgvector-0-7-0) ·
[Neon — 30× faster HNSW builds](https://neon.com/blog/pgvector-30x-faster-index-build-for-your-vector-embeddings) ·
[AWS — IVFFlat vs HNSW deep dive](https://aws.amazon.com/blogs/database/optimize-generative-ai-applications-with-pgvector-indexing-a-deep-dive-into-ivfflat-and-hnsw-techniques/) ·
[Crunchy — intro to Postgres backups](https://www.crunchydata.com/blog/introduction-to-postgres-backups) ·
[DigitalOcean — control-file error](https://docs.digitalocean.com/support/how-do-i-fix-the-pgvector-could-not-open-extension-control-file-error/) ·
[postgis/docker-postgis 16-3.5 Dockerfile](https://github.com/postgis/docker-postgis/blob/master/16-3.5/Dockerfile) ·
[GreenNode — vDB PostgreSQL supported extensions](https://docs.greennode.ai/vdb/relational-database-service-rds/postgresql-standalone/vdb-postgresql-standalone-extensions)
