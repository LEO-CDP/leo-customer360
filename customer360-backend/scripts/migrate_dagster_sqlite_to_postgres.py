#!/usr/bin/env python3
"""Best-effort migration of Dagster storage from local SQLite -> shared PostgreSQL.

Context (deployments/docs/dagster-scaling-analysis.md §4, Phase 0): the old
`dagster dev` instance persisted run / event-log / schedule storage as SQLite
files under DAGSTER_HOME. After the cutover the instance uses PostgreSQL. Dagster
has **no supported command** to move data between storage backends
(`dagster instance migrate` only migrates the SCHEMA across versions), so this
script copies the rows directly.

Approach — layout-agnostic table routing:
  * Walk every `*.db` SQLite file under the old DAGSTER_HOME (run storage,
    schedule storage, and the per-run event-log shards under history/runs/).
  * For each SQLite table, if a table of the SAME NAME exists in Postgres, copy
    the rows across the INTERSECTING columns, dropping any auto-increment `id`
    (Postgres re-assigns it; Dagster relates rows by the string `run_id`, not by
    numeric id) and using `INSERT ... ON CONFLICT DO NOTHING` so re-runs and
    cross-shard duplicates (e.g. `secondary_indexes`) are idempotent.

CAVEATS — read before trusting this in PROD:
  * Best-effort and version-sensitive. RUN IT AGAINST A STAGING COPY of the DB
    first and compare counts; Dagster's internal schema changes between versions.
  * It migrates run/event HISTORY and sensor/schedule cursors — operational
    metadata, not business data (profiles/segments/analytics live in the
    customer360 DB + S3). If the history is not worth the risk, skip this and
    start fresh; keep the SQLite backup for read-only reference.
  * The target Dagster tables must already exist — run the new image once (or
    `dagster instance migrate`) so Dagster creates them before importing.

Usage (typically inside a one-shot container of the customer360-dagster image):
  python migrate_dagster_sqlite_to_postgres.py --old-dagster-home /old/dagster_home [--dry-run]

Postgres connection is read from the same env the instance uses: DB_HOST,
DB_PORT, DB_USER, DB_PASSWORD (+ DAGSTER_PG_DB, default "dagster").
"""
from __future__ import annotations

import argparse
import glob
import os
import sqlite3
import sys

import psycopg2
from psycopg2.extras import execute_values

# Schema/bookkeeping/runtime tables that must NOT be copied across backends.
# `alembic_version` tracks the storage schema-migration revision — importing the
# SQLite value into the (independently-migrated) Postgres DB would give Alembic
# multiple heads and break future `dagster instance migrate`. The rest are
# instance identity, rebuildable indexes, or ephemeral runtime state the new
# instance owns and re-derives. Only real history (runs, events, tags, ticks,
# snapshots, instigators, assets) is migrated.
EXCLUDE_TABLES = {
    "alembic_version",
    "instance_info",
    "secondary_indexes",
    "daemon_heartbeats",
    "concurrency_limits",
    "concurrency_slots",
    "pending_steps",
    "kvs",
}


def pg_connect():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        dbname=os.environ.get("DAGSTER_PG_DB", "dagster"),
    )


def pg_tables(cur) -> dict[str, set[str]]:
    """Map {table_name: {column, ...}} for the public schema in Postgres."""
    cur.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public'"
    )
    out: dict[str, set[str]] = {}
    for table, col in cur.fetchall():
        out.setdefault(table, set()).add(col)
    return out


def sqlite_tables(con) -> list[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [r[0] for r in rows]


def migrate_table(sqlite_con, pg_cur, table: str, pg_cols: set[str], dry_run: bool) -> int:
    sqlite_con.row_factory = sqlite3.Row
    src_cols = [d[1] for d in sqlite_con.execute(f'PRAGMA table_info("{table}")').fetchall()]
    # Intersect columns; drop auto-increment id so Postgres assigns its own.
    cols = [c for c in src_cols if c in pg_cols and c != "id"]
    if not cols:
        return 0
    rows = sqlite_con.execute(f'SELECT {",".join(chr(34)+c+chr(34) for c in cols)} FROM "{table}"').fetchall()
    if not rows:
        return 0
    if dry_run:
        return len(rows)
    collist = ",".join(f'"{c}"' for c in cols)
    execute_values(
        pg_cur,
        f'INSERT INTO "{table}" ({collist}) VALUES %s ON CONFLICT DO NOTHING',
        [tuple(r) for r in rows],
        page_size=500,
    )
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--old-dagster-home", required=True, help="dir holding the old SQLite *.db files")
    ap.add_argument("--dry-run", action="store_true", help="report row counts, write nothing")
    args = ap.parse_args()

    db_files = sorted(glob.glob(os.path.join(args.old_dagster_home, "**", "*.db"), recursive=True))
    if not db_files:
        print(f"no *.db files under {args.old_dagster_home!r} — nothing to migrate")
        return 0
    print(f"found {len(db_files)} SQLite db file(s)")

    pg = pg_connect()
    pg_cur = pg.cursor()
    targets = pg_tables(pg_cur)

    totals: dict[str, int] = {}
    skipped: set[str] = set()
    for path in db_files:
        con = sqlite3.connect(path)
        try:
            for table in sqlite_tables(con):
                if table in EXCLUDE_TABLES or table not in targets:
                    skipped.add(table)
                    continue
                n = migrate_table(con, pg_cur, table, targets[table], args.dry_run)
                totals[table] = totals.get(table, 0) + n
        finally:
            con.close()

    if args.dry_run:
        pg.rollback()
        print("\nDRY RUN — would copy:")
    else:
        pg.commit()
        print("\nCOMMITTED — copied:")
    for t in sorted(totals):
        print(f"  {t:<28} {totals[t]}")
    if skipped:
        print("  (no matching Postgres table, skipped: " + ", ".join(sorted(skipped)) + ")")
    pg.close()
    print("\nDone. Verify run history + sensor cursors in the Dagster UI before relying on it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
