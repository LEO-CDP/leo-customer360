"""Create, repair and (optionally) drop time partitions for the ad tables.

Two partitioned tables are maintained in the ``leo_ads`` schema:

* ``ad_event``         monthly partitions on ``event_time``, kept 13 months
* ``serving_decision`` daily partitions on ``decided_at``, kept 90 days

Each table has a DEFAULT partition that catches rows with no matching
partition. If rows for a period already landed there (for example demo data, or
events that arrived before a partition existed), Postgres refuses to create the
period's partition. ``ensure_partition`` then moves those rows into a new table
and attaches it, so no row is lost.

Dropping old partitions is destructive, so it only happens when explicitly
enabled (``AD_PERSONALIZATION_PARTITION_DROP_ENABLED=true``); otherwise the job
only logs what it would drop. The DEFAULT partition is never dropped.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Optional

import psycopg2
from psycopg2 import sql

Log = Callable[[str], None]

# Only partitions named like the ones this module creates can ever be dropped.
_NAME_RE = re.compile(
    r"^(?P<parent>ad_event|serving_decision)_(?P<y>\d{4})_(?P<m>\d{2})(?:_(?P<d>\d{2}))?$"
)
_BOUND_RE = re.compile(r"FOR VALUES FROM \('(?P<lo>[^']+)'\) TO \('(?P<hi>[^']+)'\)")

# Postgres: "updated partition constraint for default partition ... would be
# violated by some row" is reported as check_violation.
_CHECK_VIOLATION = "23514"


@dataclass(frozen=True)
class PartitionedTable:
    """How one partitioned table is laid out and how long it is kept."""

    parent: str
    default: str
    key: str
    granularity: str  # "month" or "day"
    ahead: int  # partitions to keep created beyond the current period
    retention_months: Optional[int] = None
    retention_days: Optional[int] = None


AD_EVENT = PartitionedTable(
    parent="ad_event",
    default="ad_event_default",
    key="event_time",
    granularity="month",
    ahead=3,
    retention_months=13,
)
SERVING_DECISION = PartitionedTable(
    parent="serving_decision",
    default="serving_decision_default",
    key="decided_at",
    granularity="day",
    ahead=7,
    retention_days=90,
)
TABLES = (AD_EVENT, SERVING_DECISION)


@dataclass(frozen=True)
class PartitionSpec:
    """One wanted partition: [start, end) on a table."""

    table: PartitionedTable
    name: str
    start: date
    end: date


def add_months(d: date, months: int) -> date:
    """Add (or subtract) whole months, clamping the day to the month length."""
    index = d.year * 12 + (d.month - 1) + months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    # Last day of the target month.
    next_first = date(year + (month == 12), (month % 12) + 1, 1)
    last_day = (next_first - timedelta(days=1)).day
    return date(year, month, min(d.day, last_day))


def wanted_partitions(table: PartitionedTable, today: date) -> list[PartitionSpec]:
    """Partitions that should exist: the current period plus ``ahead`` more."""
    specs: list[PartitionSpec] = []
    if table.granularity == "month":
        first = today.replace(day=1)
        for i in range(table.ahead + 1):
            start = add_months(first, i)
            end = add_months(first, i + 1)
            specs.append(PartitionSpec(table, f"{table.parent}_{start:%Y_%m}", start, end))
    else:
        for i in range(table.ahead + 1):
            start = today + timedelta(days=i)
            end = start + timedelta(days=1)
            specs.append(
                PartitionSpec(table, f"{table.parent}_{start:%Y_%m_%d}", start, end)
            )
    return specs


def retention_cutoff(table: PartitionedTable, today: date) -> Optional[date]:
    """Partitions that end on or before this date are expired."""
    if table.retention_months is not None:
        return add_months(today, -table.retention_months)
    if table.retention_days is not None:
        return today - timedelta(days=table.retention_days)
    return None


def parse_bound(expression: str) -> Optional[tuple[date, date]]:
    """Parse ``pg_get_expr(relpartbound)`` into (start, end) dates.

    Returns None for the DEFAULT partition and anything that is not a plain
    range. The session is UTC, so the date part of the bound is the UTC date.
    """
    match = _BOUND_RE.search(expression)
    if not match:
        return None
    return (
        date.fromisoformat(match.group("lo")[:10]),
        date.fromisoformat(match.group("hi")[:10]),
    )


def partitions_to_drop(
    existing: list[tuple[str, date, date]], table: PartitionedTable, cutoff: date
) -> list[str]:
    """Names of expired partitions: this table's own, ending on or before cutoff."""
    expired = []
    for name, _start, end in existing:
        match = _NAME_RE.match(name)
        if not match or match.group("parent") != table.parent:
            continue
        if end <= cutoff:
            expired.append(name)
    return sorted(expired)


def drop_enabled_from_env() -> bool:
    """True only when dropping old partitions was explicitly switched on."""
    value = os.environ.get("AD_PERSONALIZATION_PARTITION_DROP_ENABLED", "false")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def list_partitions(cur, schema: str, table: PartitionedTable) -> list[tuple[str, date, date]]:
    """Existing range partitions of a table as (name, start, end); no DEFAULT."""
    cur.execute(
        """
        SELECT c.relname, pg_get_expr(c.relpartbound, c.oid)
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        JOIN pg_namespace n ON n.oid = p.relnamespace
        WHERE n.nspname = %s AND p.relname = %s
        """,
        (schema, table.parent),
    )
    found = []
    for name, expression in cur.fetchall():
        bounds = parse_bound(expression or "")
        if bounds is not None:
            found.append((name, bounds[0], bounds[1]))
    return found


def _relation_exists(cur, schema: str, name: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s
        """,
        (schema, name),
    )
    return cur.fetchone() is not None


def ensure_partition(cur, schema: str, spec: PartitionSpec, log: Log = print) -> str:
    """Make sure the partition exists. Returns "exists", "created" or "attached".

    "attached" means rows already sat in the DEFAULT partition for this period,
    so they were moved into a new table which was then attached.
    """
    if _relation_exists(cur, schema, spec.name):
        return "exists"

    parent = sql.Identifier(schema, spec.table.parent)
    default = sql.Identifier(schema, spec.table.default)
    child = sql.Identifier(schema, spec.name)
    low = sql.Literal(spec.start.isoformat())
    high = sql.Literal(spec.end.isoformat())

    cur.execute("SAVEPOINT ensure_partition")
    try:
        cur.execute(
            sql.SQL("CREATE TABLE {} PARTITION OF {} FOR VALUES FROM ({}) TO ({})").format(
                child, parent, low, high
            )
        )
        cur.execute("RELEASE SAVEPOINT ensure_partition")
        return "created"
    except psycopg2.Error as exc:
        cur.execute("ROLLBACK TO SAVEPOINT ensure_partition")
        if exc.pgcode != _CHECK_VIOLATION:
            raise
        log(
            f"ad_personalization: {schema}.{spec.name} blocked by rows in "
            f"{spec.table.default}; moving them"
        )

    # Repair path. Lock the default partition so no new rows for this period
    # slip in between the move and the attach; the lock is held only until the
    # surrounding transaction commits.
    cur.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(default))
    cur.execute(
        sql.SQL(
            "CREATE TABLE {} (LIKE {} INCLUDING DEFAULTS INCLUDING CONSTRAINTS)"
        ).format(child, parent)
    )
    key = sql.Identifier(spec.table.key)
    cur.execute(
        sql.SQL(
            "WITH moved AS (DELETE FROM {} WHERE {} >= {} AND {} < {} RETURNING *) "
            "INSERT INTO {} SELECT * FROM moved"
        ).format(default, key, low, key, high, child)
    )
    moved = cur.rowcount
    cur.execute(
        sql.SQL("ALTER TABLE {} ATTACH PARTITION {} FOR VALUES FROM ({}) TO ({})").format(
            parent, child, low, high
        )
    )
    log(f"ad_personalization: attached {schema}.{spec.name} ({moved} rows moved)")
    return "attached"


def run_maintenance(
    conn,
    *,
    today: Optional[date] = None,
    schema: str = "leo_ads",
    drop_enabled: bool = False,
    log: Log = print,
) -> dict:
    """Create missing partitions, repair blocked ones, and drop expired ones.

    Runs in a single transaction: either every change is applied or none.
    Returns a summary of what happened.
    """
    today = today or date.today()
    summary: dict = {
        "created": [],
        "attached": [],
        "existing": 0,
        "would_drop": [],
        "dropped": [],
    }
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL TIME ZONE 'UTC'")
            for table in TABLES:
                for spec in wanted_partitions(table, today):
                    outcome = ensure_partition(cur, schema, spec, log)
                    if outcome == "created":
                        summary["created"].append(spec.name)
                    elif outcome == "attached":
                        summary["attached"].append(spec.name)
                    else:
                        summary["existing"] += 1

                cutoff = retention_cutoff(table, today)
                if cutoff is None:
                    continue
                expired = partitions_to_drop(
                    list_partitions(cur, schema, table), table, cutoff
                )
                for name in expired:
                    if drop_enabled:
                        cur.execute(
                            sql.SQL("DROP TABLE {}").format(sql.Identifier(schema, name))
                        )
                        summary["dropped"].append(name)
                        log(f"ad_personalization: dropped expired partition {schema}.{name}")
                    else:
                        summary["would_drop"].append(name)
                        log(
                            f"ad_personalization: would drop expired partition "
                            f"{schema}.{name} (drops are disabled)"
                        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return summary
