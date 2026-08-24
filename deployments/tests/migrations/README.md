# Migration parity test

Proves `dbmate up` produces the **correct** database, by building a **throwaway**
test database with the dbmate migrations and comparing it (schema + data, scoped
to schema `customer360`) against a reference. The test DB is created and dropped
by the test — nothing persists.

```bash
deployments/tests/migrations/run-migration-test.sh
```

Requires Docker. On Windows run via Git Bash.

## Two modes (auto-selected)

### Self-contained (default — runs anywhere, no credentials)
Reference = the **legacy bootstrap** rebuilt locally in a second throwaway DB
from the frozen `database-init/*.sql` (`database-schema.sql` →
`init-core-database.sql` → `data-view-for-llm.sql`). Both DBs are fresh, so
**schema AND data** are hard pass/fail gates. This is the executable proof that
dbmate exactly reproduces the old raw-SQL bootstrap.

```bash
bash deployments/tests/migrations/run-migration-test.sh
# → MIGRATION PARITY VERIFIED — dbmate schema matches the legacy reference (schema + data). PASS
```

### Live (compare against the current UAT/prod database)
Reference = the real running database. Because a live DB holds real
transactional data, only the **schema** is a hard gate; row-count / content
differences are reported as INFO (a fresh build has only seed data). The live DB
is only ever **read** (SELECT / catalog queries) — the migrations run only
against the local throwaway DB.

The live DB is private (VPC), so open a tunnel through the bastion first, then
point the test at it:

```bash
# 1) tunnel localhost:15432 -> the private DB, through the bastion
ssh -fN -L 15432:<db-host>:<db-port> -i ~/.ssh/c360-api_ed25519 <user>@<bastion-ip>

# 2) run the test against it (READ-ONLY on the live side)
REFERENCE_DATABASE_URL="postgres://app_admin:<pw>@localhost:15432/customer360?sslmode=prefer" \
  bash deployments/tests/migrations/run-migration-test.sh
```

> The db host/port/bastion come from the same terraform outputs `run-sql.sh`
> uses (`deployments/postgres`, workspace `uat`/`prod`), which need the AWS
> credentials for the S3 backend + bastion access. Run this from an environment
> that has them (e.g. the CD runner) — not a bare laptop.

`--network host` is used to reach the tunnel, so live mode expects a Linux/CI host.

## What it compares (schema `customer360`)

| Group | Self-contained | Live |
|---|---|---|
| tables, columns, constraints, indexes, functions, triggers, RLS policies + flags, materialized views | hard | hard |
| per-table row counts, per-table content md5 | hard | info |

Auto-generated PG16 `NOT NULL` check-constraints are excluded from the
constraint comparison — their names are OID-based (e.g. `25344_26793_1_not_null`)
and differ between any two databases; column nullability is covered by the
`columns` comparison instead.

## Env / flags

| var | default | meaning |
|---|---|---|
| `REFERENCE_DATABASE_URL` | *(unset)* | set → live mode against this DB |
| `PG_IMAGE` | `customer360-postgres:migtest` (built on demand) | Postgres image with PostGIS + pgvector; reuse the repo image with `PG_IMAGE=customer360-postgres:local` |
| `DBMATE_IMAGE` | `ghcr.io/amacneil/dbmate:2` | dbmate image |
| `KEEP` | `0` | `1` → keep the container + `.artifacts/` diffs for debugging |

Exit code `0` = PASS, `1` = schema/data mismatch, `2` = setup error.
