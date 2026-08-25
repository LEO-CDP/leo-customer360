# Migration drift test

Builds a **throwaway** database with the dbmate migrations and diffs it against
the **current live database** (UAT/prod) to confirm the migrations reproduce the
live schema and to surface any drift. The test DB is created and dropped by the
test — nothing persists, and the live DB is only ever **read**.

```bash
REFERENCE_DATABASE_URL="postgres://user:pass@host:port/customer360" \
  bash deployments/tests/migrations/run-migration-test.sh
```

Requires Docker. On Windows run via Git Bash.

## Why live-only

The dbmate migrations under `database-init/db/migrations/` are now the single
source of truth for the schema (the old standalone `database-init/*.sql` files
were removed). A fresh dbmate build is only meaningfully compared against the
**real** current database, so this test requires `REFERENCE_DATABASE_URL`.

> Apply-from-zero and idempotency are already gated in CI (the `migrations` job
> in `.github/workflows/ci.yml`) — this script adds the **schema-parity / drift**
> check against a live DB.

## Connecting to the live DB (private, VPC-only)

Open a tunnel through the bastion, then point the test at it:

```bash
# 1) tunnel localhost:15432 -> the private DB, through the bastion
ssh -fN -L 15432:<db-host>:<db-port> -i ~/.ssh/c360-api_ed25519 <user>@<bastion-ip>

# 2) run the test (READ-ONLY on the live side)
REFERENCE_DATABASE_URL="postgres://app_admin:<pw>@localhost:15432/customer360?sslmode=prefer" \
  bash deployments/tests/migrations/run-migration-test.sh
```

> The db host/port/bastion come from the same terraform outputs `run-sql.sh`
> uses (`deployments/postgres`, workspace `uat`/`prod`), which need the AWS
> credentials for the S3 backend + bastion access. Run this from an environment
> that has them (e.g. the CD runner) — not a bare laptop. `--network host` is
> used to reach the tunnel, so it expects a Linux/CI host.

## What it compares (schema `customer360`)

| Group | Gate |
|---|---|
| tables, columns, constraints, indexes, functions, triggers, RLS policies + flags, materialized views | **hard** (must match) |
| per-table row counts, per-table content md5 | info (a fresh build has only seed data) |

Auto-generated PG16 `NOT NULL` check-constraints are excluded from the
constraint comparison — their names are OID-based (e.g. `25344_26793_1_not_null`)
and differ between any two databases; column nullability is covered by the
`columns` comparison instead.

## Env / flags

| var | default | meaning |
|---|---|---|
| `REFERENCE_DATABASE_URL` | **required** | the live DB to diff against (read-only) |
| `PG_IMAGE` | `customer360-postgres:migtest` (built on demand) | Postgres image with PostGIS + pgvector; reuse the repo image with `PG_IMAGE=customer360-postgres:local` |
| `DBMATE_IMAGE` | `ghcr.io/amacneil/dbmate:2` | dbmate image |
| `KEEP` | `0` | `1` → keep the container + `.artifacts/` diffs for debugging |

Exit code `0` = PASS (schema matches), `1` = schema mismatch, `2` = setup error.
