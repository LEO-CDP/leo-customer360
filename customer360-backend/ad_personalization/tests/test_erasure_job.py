"""Tests for the erasure job and the profile job's erasure and eligibility rules.

Pure logic and the Dagster wiring always run. The database group is opt-in; see
tests/conftest.py.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest
from conftest import NOW, SCHEMA, requires_db
from psycopg2.extras import Json

import dagster_defs
from ad_personalization import erasure
from ad_personalization import profile_aggregation as pa

KEY = b"\x11" * 16
OTHER_KEY = b"\x22" * 16
WEIGHTS = pa.load_weights()


# --------------------------------------------------------------------------
# Pure logic and wiring
# --------------------------------------------------------------------------


def test_deadlines_are_72_hours_urgent_and_30_days_otherwise():
    assert erasure.deadline_for(NOW, True) == NOW + timedelta(hours=72)
    assert erasure.deadline_for(NOW, False) == NOW + timedelta(days=30)


def test_failed_requests_are_retried():
    assert set(erasure.OPEN_STATUSES) == {"pending", "redis_purged", "failed"}


def test_every_visitor_table_is_erased():
    assert {key for key, _ in erasure.ERASED_TABLES} == {
        "ad_event", "serving_decision", "visitor_behaviour_profile",
        "consent_record", "consent_history", "user_serving_key",
    }


def test_the_job_is_registered_and_scheduled_hourly():
    schedule = dagster_defs.ad_erasure_schedule
    assert schedule.job_name == "ad_erasure_job"
    assert schedule.cron_schedule == "20 * * * *"
    repository = dagster_defs.defs.get_repository_def()
    assert "ad_erasure_job" in {job.name for job in repository.get_all_jobs()}


def test_the_op_runs_with_a_stubbed_database(monkeypatch):
    class Conn:
        committed = closed = False

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("must not roll back on success")

        def close(self):
            self.closed = True

    conn = Conn()
    summary = {"processed": 2, "completed": 2, "failed": 0, "overdue": 0}
    monkeypatch.setattr(dagster_defs, "connect", lambda: conn)
    monkeypatch.setattr(dagster_defs, "process_erasure_requests", lambda c, **kw: summary)

    result = dagster_defs.ad_erasure_job.execute_in_process()

    assert result.success
    assert result.output_for_node("erasure_op") == summary
    assert conn.committed and conn.closed


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------


def _request(db, tenant, key=KEY, *, urgent=False, status="pending", requested_at=None):
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO {SCHEMA}.erasure_request (tenant_id, user_key_hash, urgent, status, requested_at) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING erasure_id",
            (tenant.tenant_id, key, urgent, status, requested_at or NOW),
        )
        return cur.fetchone()[0]


def _request_row(db, erasure_id):
    with db.cursor() as cur:
        cur.execute(
            f"SELECT status, completed_at, deleted_counts, failure_reason "
            f"FROM {SCHEMA}.erasure_request WHERE erasure_id = %s",
            (erasure_id,),
        )
        return cur.fetchone()


def _count(db, table, tenant, key):
    with db.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {SCHEMA}.{table} WHERE tenant_id = %s AND user_key_hash = %s",
            (tenant.tenant_id, key),
        )
        return cur.fetchone()[0]


def _give_visitor_data(db, tenant, key=KEY, serving_key=True):
    """One row in every table that can hold a visitor, events across partitions.

    ``user_serving_key`` is keyed by the hash alone (real keys are tenant-derived,
    so two tenants never share one); pass ``serving_key=False`` to reuse a key.
    """
    ad = tenant.add_ad("ad_" + key.hex()[:6])
    request_id = tenant.event(ad, "click", NOW, key=key)  # current month's partition
    tenant.event(ad, "impression", datetime(2020, 1, 15, tzinfo=timezone.utc), key=key)  # default partition
    tenant.decision(request_id, "personalised", NOW)
    with db.cursor() as cur:
        cur.execute(
            f"UPDATE {SCHEMA}.serving_decision SET user_key_hash = %s WHERE request_id = %s",
            (key, request_id),
        )
        cur.execute(
            f"INSERT INTO {SCHEMA}.visitor_behaviour_profile (tenant_id, user_key_hash, affinities, event_count) "
            "VALUES (%s, %s, %s, 2)",
            (tenant.tenant_id, key, Json({})),
        )
        cur.execute(
            f"INSERT INTO {SCHEMA}.consent_history (tenant_id, user_key_hash, status) VALUES (%s, %s, 'granted')",
            (tenant.tenant_id, key),
        )
        if serving_key:
            cur.execute(
                f"INSERT INTO {SCHEMA}.user_serving_key (user_key_hash, tenant_id) VALUES (%s, %s)",
                (key, tenant.tenant_id),
            )
    tenant.consent(key, "granted")
    return ad


def _all_counts(db, tenant, key=KEY):
    return {table: _count(db, table, tenant, key) for _, table in erasure.ERASED_TABLES}


def _run(db, **kwargs):
    return erasure.process_erasure_requests(db, schema=SCHEMA, now=NOW, log=lambda _m: None, **kwargs)


# --------------------------------------------------------------------------
# Erasure job (database, opt-in)
# --------------------------------------------------------------------------


@requires_db
def test_every_trace_of_the_visitor_is_removed_and_counted(db, tenant_factory):
    tenant = tenant_factory()
    _give_visitor_data(db, tenant)
    assert all(n >= 1 for n in _all_counts(db, tenant).values())
    erasure_id = _request(db, tenant)

    summary = _run(db)

    assert summary["completed"] == 1 and summary["failed"] == 0
    assert all(n == 0 for n in _all_counts(db, tenant).values())
    status, completed_at, counts, reason = _request_row(db, erasure_id)
    assert status == "completed" and completed_at == NOW and reason is None
    assert counts == {
        "ad_event": 2, "serving_decision": 1, "visitor_behaviour_profile": 1,
        "consent_record": 1, "consent_history": 1, "user_serving_key": 1,
    }


@requires_db
def test_events_in_the_default_partition_are_erased_too(db, tenant_factory):
    tenant = tenant_factory()
    ad = tenant.add_ad("old")
    tenant.event(ad, "click", datetime(2019, 6, 1, tzinfo=timezone.utc), key=KEY)
    _request(db, tenant)

    _run(db)

    assert _count(db, "ad_event", tenant, KEY) == 0


@requires_db
def test_a_visitor_with_no_data_still_ends_completed(db, tenant_factory):
    tenant = tenant_factory()
    erasure_id = _request(db, tenant)

    _run(db)

    status, completed_at, counts, _ = _request_row(db, erasure_id)
    assert status == "completed" and completed_at == NOW
    assert set(counts.values()) == {0}


@requires_db
def test_only_that_tenants_rows_and_that_visitors_rows_are_removed(db, tenant_factory):
    first, second = tenant_factory(), tenant_factory()
    _give_visitor_data(db, first, KEY)
    _give_visitor_data(db, first, OTHER_KEY)  # a different visitor, same tenant
    _give_visitor_data(db, second, KEY, serving_key=False)  # the same key, another tenant
    _request(db, first, KEY)

    _run(db)

    assert all(n == 0 for n in _all_counts(db, first, KEY).values())
    assert all(n >= 1 for n in _all_counts(db, first, OTHER_KEY).values())
    survivors = _all_counts(db, second, KEY)
    assert survivors.pop("user_serving_key") == 0  # none was created for them
    assert all(n >= 1 for n in survivors.values())


@requires_db
def test_urgent_requests_go_first_then_oldest(db, tenant_factory):
    tenant = tenant_factory()
    oldest = _request(db, tenant, b"\x01" * 16, requested_at=NOW - timedelta(days=3))
    newer = _request(db, tenant, b"\x02" * 16, requested_at=NOW - timedelta(days=1))
    urgent = _request(db, tenant, b"\x03" * 16, urgent=True, requested_at=NOW - timedelta(hours=1))

    order = [r["erasure_id"] for r in erasure.open_requests(db, SCHEMA) if r["tenant_id"] == tenant.tenant_id]

    assert order == [urgent, oldest, newer]


@requires_db
def test_completed_requests_are_left_alone(db, tenant_factory):
    tenant = tenant_factory()
    _give_visitor_data(db, tenant)
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO {SCHEMA}.erasure_request (tenant_id, user_key_hash, status, completed_at, deleted_counts) "
            "VALUES (%s, %s, 'completed', %s, %s)",
            (tenant.tenant_id, KEY, NOW, Json({})),
        )

    summary = _run(db)

    assert summary["processed"] == 0
    assert _count(db, "ad_event", tenant, KEY) >= 1


@requires_db
def test_redis_purged_and_failed_requests_are_processed_too(db, tenant_factory):
    tenant = tenant_factory()
    purged = _request(db, tenant, b"\x01" * 16, status="redis_purged")
    failed = _request(db, tenant, b"\x02" * 16, status="failed")

    _run(db)

    assert _request_row(db, purged)[0] == "completed"
    assert _request_row(db, failed)[0] == "completed"
    assert _request_row(db, failed)[3] is None  # the earlier failure reason is cleared


@requires_db
def test_a_failure_is_recorded_retried_and_does_not_stop_the_others(db, tenant_factory, monkeypatch):
    tenant = tenant_factory()
    _give_visitor_data(db, tenant, KEY)
    _give_visitor_data(db, tenant, OTHER_KEY)
    broken = _request(db, tenant, KEY, requested_at=NOW - timedelta(days=2))
    healthy = _request(db, tenant, OTHER_KEY, requested_at=NOW - timedelta(days=1))
    real = erasure._erase_visitor

    def flaky(conn, schema, tenant_id, user_key):
        if user_key == KEY:
            real(conn, schema, tenant_id, user_key)  # partial work that must be rolled back
            raise RuntimeError("disk on fire")
        return real(conn, schema, tenant_id, user_key)

    monkeypatch.setattr(erasure, "_erase_visitor", flaky)

    summary = _run(db)

    assert summary["failed"] == 1 and summary["completed"] == 1
    status, completed_at, counts, reason = _request_row(db, broken)
    assert status == "failed" and completed_at is None and counts is None
    assert "disk on fire" in reason
    assert _request_row(db, healthy)[0] == "completed"
    assert all(n >= 1 for n in _all_counts(db, tenant, KEY).values())  # rolled back, nothing half-erased

    monkeypatch.setattr(erasure, "_erase_visitor", real)
    _run(db)  # the next run retries it
    assert _request_row(db, broken)[0] == "completed"
    assert all(n == 0 for n in _all_counts(db, tenant, KEY).values())


@requires_db
@pytest.mark.parametrize(
    "urgent, age, overdue",
    [
        (True, timedelta(hours=71), False),
        (True, timedelta(hours=73), True),
        (False, timedelta(days=29), False),
        (False, timedelta(days=31), True),
    ],
)
def test_a_request_past_its_limit_is_logged_as_an_error_but_still_processed(
    db, tenant_factory, caplog, urgent, age, overdue
):
    tenant = tenant_factory()
    erasure_id = _request(db, tenant, urgent=urgent, requested_at=NOW - age)

    with caplog.at_level(logging.ERROR, logger=erasure.logger.name):
        summary = _run(db)

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert bool(errors) is overdue
    assert summary["overdue"] == (1 if overdue else 0)
    assert _request_row(db, erasure_id)[0] == "completed"


# --------------------------------------------------------------------------
# Profile job: erasure skip and eligibility (T082)
# --------------------------------------------------------------------------


def _rebuild(db):
    return pa.rebuild_profiles(db, schema=SCHEMA, now=NOW, weights=WEIGHTS, log=lambda _m: None)


@requires_db
def test_a_visitor_with_an_open_erasure_request_is_not_profiled(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    ad = tenant.add_ad("ad_one", tags=["travel"])
    tenant.event(ad, "click", NOW, key=KEY)
    tenant.event(ad, "click", NOW, key=OTHER_KEY)
    _request(db, tenant, KEY, status="redis_purged")

    _rebuild(db)

    assert set(tenant.profiles()) == {OTHER_KEY}


@requires_db
@pytest.mark.parametrize("status", ["pending", "redis_purged", "failed"])
def test_an_existing_profile_is_removed_while_a_request_is_open_and_never_recreated(db, tenant_factory, status):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    ad = tenant.add_ad("ad_one", tags=["travel"])
    tenant.event(ad, "click", NOW, key=KEY)
    _rebuild(db)
    assert KEY in tenant.profiles()

    _request(db, tenant, KEY, status=status)
    _rebuild(db)
    _rebuild(db)

    assert KEY not in tenant.profiles()


@requires_db
def test_a_completed_request_does_not_block_new_profiling_of_new_behaviour(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    ad = tenant.add_ad("ad_one", tags=["travel"])
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO {SCHEMA}.erasure_request (tenant_id, user_key_hash, status, completed_at, deleted_counts) "
            "VALUES (%s, %s, 'completed', %s, %s)",
            (tenant.tenant_id, KEY, NOW - timedelta(days=1), Json({})),
        )
    tenant.event(ad, "click", NOW, key=KEY)  # the visitor came back afterwards

    _rebuild(db)

    assert KEY in tenant.profiles()


@requires_db
def test_the_erasure_request_of_another_tenant_does_not_block_profiling(db, tenant_factory):
    first, second = tenant_factory(), tenant_factory()
    for tenant in (first, second):
        tenant.settings(consent_mode="opt_out")
        tenant.event(tenant.add_ad("ad_one"), "click", NOW, key=KEY)
    _request(db, first, KEY)

    _rebuild(db)

    assert first.profiles() == {}
    assert KEY in second.profiles()


@requires_db
def test_in_an_opt_in_tenant_a_profile_goes_when_there_is_no_grant_and_is_never_recreated(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_in")
    ad = tenant.add_ad("ad_one", tags=["travel"])
    tenant.consent(KEY, "granted")
    tenant.event(ad, "click", NOW, key=KEY)
    _rebuild(db)
    assert KEY in tenant.profiles()

    with db.cursor() as cur:  # the record disappears (erased, or the tenant switched to opt-in)
        cur.execute(
            f"DELETE FROM {SCHEMA}.consent_record WHERE tenant_id = %s AND user_key_hash = %s",
            (tenant.tenant_id, KEY),
        )
    _rebuild(db)
    _rebuild(db)

    assert KEY not in tenant.profiles()


@requires_db
def test_a_denied_visitor_loses_the_profile_and_it_is_not_recreated(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    ad = tenant.add_ad("ad_one", tags=["travel"])
    tenant.event(ad, "click", NOW, key=KEY)
    _rebuild(db)
    assert KEY in tenant.profiles()

    tenant.consent(KEY, "denied")
    _rebuild(db)
    _rebuild(db)

    assert KEY not in tenant.profiles()
