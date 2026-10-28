"""Tests for the ad partition maintenance job.

The first group is pure logic (dates, names, retention) and always runs.

The second group needs a PostgreSQL database. It never touches ``leo_ads``:
each test creates its own throw-away schema with minimal copies of the two
partitioned tables and drops it afterwards. To avoid ever running against a
real database by accident it is opt-in:

    AD_PERSONALIZATION_ALLOW_DB_TESTS=1 DB_HOST=127.0.0.1 DB_PORT=55432 \
        DB_PASSWORD=... python -m pytest tests/test_partition_maintenance.py
"""

import os
import uuid
from datetime import date

import pytest
from psycopg2 import sql

from ad_personalization import partition_maintenance as pm
from ad_personalization.db import connect

TODAY = date(2026, 10, 18)


# --------------------------------------------------------------------------
# Pure logic
# --------------------------------------------------------------------------


def test_add_months_rolls_the_year_and_clamps_the_day():
    assert pm.add_months(date(2026, 10, 18), 3) == date(2027, 1, 18)
    assert pm.add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert pm.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert pm.add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
    assert pm.add_months(date(2026, 10, 18), -13) == date(2025, 9, 18)
    assert pm.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)  # leap year


def test_monthly_partitions_cover_the_current_month_plus_three():
    specs = pm.wanted_partitions(pm.AD_EVENT, date(2026, 11, 20))
    assert [s.name for s in specs] == [
        "ad_event_2026_11",
        "ad_event_2026_12",
        "ad_event_2027_01",
        "ad_event_2027_02",
    ]
    assert (specs[0].start, specs[0].end) == (date(2026, 11, 1), date(2026, 12, 1))
    assert specs[1].end == date(2027, 1, 1)  # December rolls into January


def test_daily_partitions_cover_today_plus_seven():
    specs = pm.wanted_partitions(pm.SERVING_DECISION, date(2026, 10, 28))
    assert len(specs) == 8
    assert specs[0].name == "serving_decision_2026_10_28"
    assert specs[-1].name == "serving_decision_2026_11_04"
    assert specs[3].end == date(2026, 11, 1)  # 31 October rolls into November


def test_retention_cutoffs():
    assert pm.retention_cutoff(pm.AD_EVENT, TODAY) == date(2025, 9, 18)  # 13 months
    assert pm.retention_cutoff(pm.SERVING_DECISION, TODAY) == date(2026, 7, 20)  # 90 days


def test_parse_bound_reads_ranges_and_ignores_the_default_partition():
    expression = "FOR VALUES FROM ('2026-08-01 00:00:00+00') TO ('2026-09-01 00:00:00+00')"
    assert pm.parse_bound(expression) == (date(2026, 8, 1), date(2026, 9, 1))
    assert pm.parse_bound("DEFAULT") is None


def test_only_this_tables_expired_named_partitions_are_dropped():
    existing = [
        ("ad_event_2025_08", date(2025, 8, 1), date(2025, 9, 1)),  # ended before the cutoff
        ("ad_event_2025_09", date(2025, 9, 1), date(2025, 10, 1)),  # still partly in the window
        ("ad_event_2026_10", date(2026, 10, 1), date(2026, 11, 1)),
        ("somebody_elses_table_2020_01", date(2020, 1, 1), date(2020, 2, 1)),
        ("serving_decision_2020_01_01", date(2020, 1, 1), date(2020, 1, 2)),
    ]
    cutoff = date(2025, 9, 18)
    assert pm.partitions_to_drop(existing, pm.AD_EVENT, cutoff) == ["ad_event_2025_08"]
    # A table never drops another table's partitions, and unknown names are left alone.
    assert pm.partitions_to_drop(existing, pm.SERVING_DECISION, date(2026, 7, 20)) == [
        "serving_decision_2020_01_01"
    ]


@pytest.mark.parametrize(
    "value,expected",
    [(None, False), ("", False), ("false", False), ("0", False), ("true", True), ("1", True), ("YES", True)],
)
def test_dropping_is_off_unless_explicitly_enabled(monkeypatch, value, expected):
    monkeypatch.delenv("AD_PERSONALIZATION_PARTITION_DROP_ENABLED", raising=False)
    if value is not None:
        monkeypatch.setenv("AD_PERSONALIZATION_PARTITION_DROP_ENABLED", value)
    assert pm.drop_enabled_from_env() is expected


# --------------------------------------------------------------------------
# Database behaviour (opt-in, throw-away schema)
# --------------------------------------------------------------------------

requires_db = pytest.mark.skipif(
    os.environ.get("AD_PERSONALIZATION_ALLOW_DB_TESTS") != "1",
    reason="set AD_PERSONALIZATION_ALLOW_DB_TESTS=1 (and DB_* env) to run against a disposable database",
)


def _quiet(_message: str) -> None:
    return None


@pytest.fixture
def scratch_schema():
    """A private schema holding minimal copies of the two partitioned tables."""
    conn = connect()
    schema = "adp_test_" + uuid.uuid4().hex[:8]
    ident = sql.Identifier(schema)
    with conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA {}").format(ident))
        for template in (
            "CREATE TABLE {s}.ad_event (event_time timestamptz NOT NULL, "
            "event_id uuid NOT NULL DEFAULT gen_random_uuid(), payload text, "
            "PRIMARY KEY (event_time, event_id)) PARTITION BY RANGE (event_time)",
            "CREATE TABLE {s}.ad_event_default PARTITION OF {s}.ad_event DEFAULT",
            "CREATE TABLE {s}.serving_decision (request_id uuid NOT NULL, "
            "decided_at timestamptz NOT NULL, payload text, "
            "PRIMARY KEY (request_id, decided_at)) PARTITION BY RANGE (decided_at)",
            "CREATE TABLE {s}.serving_decision_default PARTITION OF "
            "{s}.serving_decision DEFAULT",
        ):
            cur.execute(sql.SQL(template).format(s=ident))
    conn.commit()
    try:
        yield conn, schema
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(ident))
        conn.commit()
        conn.close()


def _count(conn, schema: str, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, table)))
        return cur.fetchone()[0]


def _names(conn, schema: str, table: pm.PartitionedTable) -> set[str]:
    with conn.cursor() as cur:
        return {name for name, _s, _e in pm.list_partitions(cur, schema, table)}


@requires_db
def test_creates_every_wanted_partition(scratch_schema):
    conn, schema = scratch_schema
    summary = pm.run_maintenance(conn, today=TODAY, schema=schema, log=_quiet)

    assert len(summary["created"]) == 4 + 8
    assert summary["attached"] == []
    assert summary["existing"] == 0
    assert _names(conn, schema, pm.AD_EVENT) == {
        "ad_event_2026_10",
        "ad_event_2026_11",
        "ad_event_2026_12",
        "ad_event_2027_01",
    }
    assert len(_names(conn, schema, pm.SERVING_DECISION)) == 8


@requires_db
def test_a_second_run_changes_nothing(scratch_schema):
    conn, schema = scratch_schema
    pm.run_maintenance(conn, today=TODAY, schema=schema, log=_quiet)
    second = pm.run_maintenance(conn, today=TODAY, schema=schema, log=_quiet)

    assert second["created"] == []
    assert second["attached"] == []
    assert second["existing"] == 4 + 8


@requires_db
def test_rows_already_in_the_default_partition_are_moved_not_lost(scratch_schema):
    conn, schema = scratch_schema
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "INSERT INTO {}.ad_event (event_time, payload) VALUES "
                "('2026-10-05T10:00:00Z', 'oct'), "
                "('2026-11-02T10:00:00Z', 'nov-a'), "
                "('2026-11-20T10:00:00Z', 'nov-b'), "
                "('2025-01-15T10:00:00Z', 'ancient')"
            ).format(sql.Identifier(schema))
        )
    conn.commit()
    assert _count(conn, schema, "ad_event_default") == 4

    summary = pm.run_maintenance(conn, today=TODAY, schema=schema, log=_quiet)

    assert sorted(summary["attached"]) == ["ad_event_2026_10", "ad_event_2026_11"]
    assert _count(conn, schema, "ad_event_2026_10") == 1
    assert _count(conn, schema, "ad_event_2026_11") == 2
    # The row that belongs to no partition stays where it was; nothing is lost.
    assert _count(conn, schema, "ad_event_default") == 1
    assert _count(conn, schema, "ad_event") == 4

    again = pm.run_maintenance(conn, today=TODAY, schema=schema, log=_quiet)
    assert again["created"] == [] and again["attached"] == []


@requires_db
def test_expired_partitions_are_only_dropped_when_enabled(scratch_schema):
    conn, schema = scratch_schema
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "CREATE TABLE {s}.ad_event_2024_01 PARTITION OF {s}.ad_event "
                "FOR VALUES FROM ('2024-01-01') TO ('2024-02-01')"
            ).format(s=sql.Identifier(schema))
        )
    conn.commit()

    dry_run = pm.run_maintenance(conn, today=TODAY, schema=schema, log=_quiet)
    assert dry_run["would_drop"] == ["ad_event_2024_01"]
    assert dry_run["dropped"] == []
    assert "ad_event_2024_01" in _names(conn, schema, pm.AD_EVENT)

    enabled = pm.run_maintenance(
        conn, today=TODAY, schema=schema, drop_enabled=True, log=_quiet
    )
    assert enabled["dropped"] == ["ad_event_2024_01"]
    names = _names(conn, schema, pm.AD_EVENT)
    assert "ad_event_2024_01" not in names
    assert "ad_event_2026_10" in names  # current data is never touched
    assert _count(conn, schema, "ad_event_default") == 0  # default partition survives
