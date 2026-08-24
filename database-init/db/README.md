# Database migrations (dbmate)

PostgreSQL schema changes for the `customer360` database are managed with
[dbmate](https://github.com/amacneil/dbmate) — plain-SQL, ordered, tracked
migrations. See the design write-up in
[`docs/research-tech/sql-migration-tool-recommendation.md`](../../docs/research-tech/sql-migration-tool-recommendation.md).

## Layout

```
database-init/db/
├── README.md
├── schema.sql          # generated snapshot (dbmate dump); commit after changes
└── migrations/
    ├── 20260824090000_baseline_schema.sql          # full schema (frozen baseline)
    ├── 20260824090100_harden_tenant_rls_policies.sql
    └── 20260824090200_seed_core_data.sql           # core seed (default tenant, catalogs)
```

Each migration has a `-- migrate:up` and a `-- migrate:down` section.

### Idempotency (verified)

All three migrations are idempotent, so `dbmate up` is safe on both a fresh
database and an already-provisioned one (the existing-DB adoption case):

- baseline: `CREATE TABLE/SCHEMA/EXTENSION IF NOT EXISTS`, `CREATE OR REPLACE
  FUNCTION`, `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER`, all 115 indexes
  `CREATE INDEX IF NOT EXISTS`, RLS policies `DROP POLICY IF EXISTS` + `CREATE`
- RLS hardening: `DROP POLICY IF EXISTS` + `CREATE`
- seed: every `INSERT ... ON CONFLICT DO NOTHING/UPDATE`

Proven by replaying all three against an already-populated DB with `schema_migrations`
truncated — zero errors.

### Rollback safety (verified)

- `dbmate down` rolls back **one** migration (the latest applied), inside a
  transaction — a failed down rolls back atomically, never leaving partial state.
- seed / RLS-hardening downs are **non-destructive no-ops** (they don't delete
  reference data); `down` then `up` round-trips cleanly.
- The **baseline down is guarded**: it would `DROP SCHEMA customer360 CASCADE`
  (total data loss), so it **refuses by default** and only runs when the operator
  opts in on the connection:

  ```bash
  # Intentional full teardown (dev): opt in via the libpq options param.
  DATABASE_URL="postgres://postgres:$DB_PASSWORD@localhost:5432/customer360?sslmode=disable&options=-c%20app.allow_destructive_down%3Dtrue" \
    dbmate --no-dump-schema down
  ```

  Without the flag, `dbmate down` on the baseline errors, the schema is left
  fully intact, and the migration stays marked applied.

## How it runs

- **Docker Compose (dev / local / UAT-on-compose):** the one-shot `migrate`
  service in `docker-compose.yml` runs `dbmate up` after Postgres is healthy and
  before the API, Dagster, and the demo seeder start
  (`depends_on: migrate: { condition: service_completed_successfully }`).
  Nothing to run by hand — `docker compose up` applies migrations automatically.
- **Postgres image:** `/docker-entrypoint-initdb.d` now bootstraps **only the
  extensions** (`postgres/init/00-extensions.sql`). Schema + seed come from
  dbmate. The old initdb.d schema copy only ran once (empty volume) and silently
  ignored later changes — that is what this replaces.

## Common commands

Run against a database with a `DATABASE_URL` (see `.env.example`). Locally you
can use the dbmate container so you don't need the binary installed:

```bash
# Apply all pending migrations
docker compose run --rm migrate

# Create a new migration (writes a timestamped up/down skeleton)
docker run --rm -v "$PWD/database-init/db:/db" ghcr.io/amacneil/dbmate:2 \
  --migrations-dir /db/migrations new add_loyalty_tier

# Status / pending
docker run --rm --network customer360-network \
  -e DATABASE_URL="postgres://postgres:$DB_PASSWORD@postgres:5432/customer360?sslmode=disable" \
  -v "$PWD/database-init/db:/db:ro" ghcr.io/amacneil/dbmate:2 \
  --no-dump-schema --migrations-dir /db/migrations status

# Roll back the last migration (dev only — NEVER in prod for the baseline)
#   its down is DROP SCHEMA customer360 CASCADE.
docker compose run --rm migrate --migrations-dir /db/migrations down
```

> On Git Bash (Windows), prefix commands that pass `/db/...` args with
> `MSYS_NO_PATHCONV=1` so the POSIX path isn't rewritten to a Windows path.

## Rules

1. **Append-only.** Never edit a migration that has already run anywhere shared
   (including the baseline). Write a new migration instead.
2. **Every `up` needs a real `down`** (or an explicit no-op comment).
3. **Concurrent index?** Add `-- migrate:up transaction:false` on the header line
   (Atlas lint in CI flags when you forget). None of the current migrations need it.
4. **Commit `schema.sql`** after changing migrations: run `dbmate dump` against a
   freshly-migrated DB and commit the diff. CI can diff it to catch drift.
5. **Never manage Keycloak's DB** (`db_keycloak`) — Keycloak self-migrates.
6. The legacy `database-init/database-schema.sql` and `init-core-database.sql`
   are **frozen reference snapshots** — editing them changes nothing. Change the
   schema through a new migration here.

## Production / UAT via `deployments/postgres/run-sql.sh`

The DB is private (reachable only from the VPC bastion), so that script runs
dbmate **on the bastion**, mirroring how it already runs psql there:

1. **Stage 1 — infra SQL via psql:** `postgres/**/*.sql` (extensions, the
   `db_keycloak` database). These are not dbmate-managed.
2. **Stage 2 — app schema via dbmate:** it `scp`s `database-init/db/migrations/`
   to the bastion, self-installs the dbmate static binary (arch-matched), and
   runs `dbmate --no-dump-schema --wait up`, then cleans up. Only up-migrations
   run — dbmate never executes a down here. Because every migration is
   idempotent, this is safe to re-run on the already-provisioned prod DB.

The `DATABASE_URL` (with the password) is built on the runner and shipped
base64-encoded so no secret lands on the bastion command line.

> **Never** feed the dbmate migration files to `psql` directly — psql would run
> the `-- migrate:down` sections too (e.g. `DROP SCHEMA customer360 CASCADE`).
> dbmate parses the up/down markers and runs only the requested half; raw psql
> does not.
