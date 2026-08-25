"""Tests for tracking-log aggregation and its Dagster wrapper."""

from io import BytesIO
from unittest.mock import MagicMock

import dagster_defs
from source_analytics import tracking_log_aggregation as aggregation


class FakeCursor:
    def __init__(self, fetch_results, rowcount=1):
        self.fetch_results = iter(fetch_results)
        self.execute_calls = []
        self.rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, query, params=None):
        self.execute_calls.append((query, params))

    def fetchall(self):
        return next(self.fetch_results)


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.commits = 0
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class FakePaginator:
    def __init__(self, pages, calls):
        self.pages = pages
        self.calls = calls

    def paginate(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages


class FakeS3:
    def __init__(self, objects):
        self.objects = objects
        self.get_calls = []
        self.paginate_calls = []

    def get_paginator(self, _name):
        return FakePaginator(
            [
                {
                    "Contents": [
                        {"Key": key}
                        for key in [
                            "2026-08-25-08/first.jsonl",
                            "not-an-hour/readme.txt",
                            "2026-08-25-09/second.jsonl",
                        ]
                    ]
                }
            ],
            self.paginate_calls,
        )

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        return {"Body": self.objects[kwargs["Key"]]}


class FakeRedis:
    def __init__(self, results):
        self.results = iter(results)
        self.eval_calls = []
        self.states = {}
        self.locked = False

    def set(self, _key, _value, nx=False, ex=None):
        assert nx is True
        assert ex == aggregation.LOCK_TTL_SECONDS
        if self.locked:
            return False
        self.locked = True
        return True

    def hset(self, key, mapping):
        self.states.setdefault(key, {}).update(mapping)

    def hgetall(self, key):
        return self.states.get(key, {})

    def exists(self, _key):
        return int(self.locked)

    def eval(self, *args):
        self.eval_calls.append(args)
        script = args[0]
        if "EXPIRE" in script:
            return 1
        if "DEL" in script:
            self.locked = False
            return 1
        return next(self.results)


def test_fetch_data_sources_honors_tenant_rls_and_global_limit():
    cursor = FakeCursor(
        [
            [("tenant-b",), ("tenant-a",)],
            [("source-3", "tenant-b"), ("source-1", "tenant-b")],
            [("source-2", "tenant-a"), ("source-4", "tenant-a")],
        ]
    )
    connection = FakeConnection(cursor)

    result = aggregation.fetch_data_sources(connection, limit=3)

    assert result == [
        ("source-1", "tenant-b"),
        ("source-2", "tenant-a"),
        ("source-3", "tenant-b"),
    ]
    assert cursor.execute_calls[1][1] == ("tenant-b",)
    assert cursor.execute_calls[2][1] == (3,)
    assert cursor.execute_calls[3][1] == ("tenant-a",)


def test_count_jsonl_records_ignores_blank_lines_and_requires_objects():
    body = BytesIO(b'{"event": "page_view"}\n\n{"event": "purchase"}\n')

    assert aggregation.count_jsonl_records(body, "hour/events.jsonl") == 2


def test_iter_hourly_objects_uses_last_processed_object_as_start_after():
    s3 = FakeS3({})

    aggregation.iter_hourly_objects(
        s3,
        "data-tracking-source-1",
        start_after="2026-08-25-08/first.jsonl",
    )

    assert s3.paginate_calls == [
        {
            "Bucket": "data-tracking-source-1",
            "StartAfter": "2026-08-25-08/first.jsonl",
        }
    ]


def test_process_tracking_logs_counts_new_objects_and_skips_checkpointed_objects(
    monkeypatch,
):
    cursor = FakeCursor([[]], rowcount=1)
    connection = FakeConnection(cursor)
    s3 = FakeS3(
        {
            "2026-08-25-08/first.jsonl": BytesIO(b'{"event": "page_view"}\n{"event": "click"}\n'),
            "2026-08-25-09/second.jsonl": BytesIO(b'{"event": "purchase"}\n'),
        }
    )
    redis_client = FakeRedis([1, 0])
    monkeypatch.setattr(
        aggregation,
        "fetch_data_sources",
        MagicMock(return_value=[("source-1", "tenant-1")]),
    )

    summary = aggregation.process_tracking_logs(
        s3_client=s3,
        redis_client=redis_client,
        db_connection=connection,
    )

    assert summary == {
        "sources_processed": 1,
        "sources_skipped_running": 0,
        "objects_processed": 1,
        "events_added": 2,
    }
    assert len(s3.get_calls) == 2
    increment_call = next(call for call in redis_client.eval_calls if "HINCRBY" in call[0])
    assert increment_call[4:7] == ("1", "tracked-event", "2")
    assert "total_tracked_event" in cursor.execute_calls[-1][0]
    assert cursor.execute_calls[-1][1] == (2, "source-1", "tenant-1")
    assert s3.paginate_calls == [{"Bucket": "data-tracking-source-1"}]
    assert redis_client.states["analytics:data-source-state:source-1"][
        "last_processed_hour"
    ] == "2026-08-25-09"
    assert redis_client.states["analytics:data-source-state:source-1"][
        "status"
    ] == "completed"
    assert connection.commits == 1
    assert connection.closed is False


def test_analytics_job_returns_aggregation_summary(monkeypatch):
    summary = {
        "sources_processed": 2,
        "sources_skipped_running": 0,
        "objects_processed": 4,
        "events_added": 17,
    }
    process = MagicMock(return_value=summary)
    monkeypatch.setattr(dagster_defs, "process_tracking_logs", process)

    result = dagster_defs.analytics_job.execute_in_process()

    assert result.success
    assert result.output_for_node("aggregate_tracking_logs_op") == summary
    process.assert_called_once()
    assert callable(process.call_args.kwargs["log"])


def test_process_tracking_logs_skips_a_locked_source(monkeypatch):
    cursor = FakeCursor([[]], rowcount=1)
    connection = FakeConnection(cursor)
    redis_client = FakeRedis([])
    redis_client.locked = True
    monkeypatch.setattr(
        aggregation,
        "fetch_data_sources",
        MagicMock(return_value=[("source-1", "tenant-1")]),
    )

    summary = aggregation.process_tracking_logs(
        s3_client=MagicMock(),
        redis_client=redis_client,
        db_connection=connection,
    )

    assert summary["sources_processed"] == 0
    assert summary["sources_skipped_running"] == 1
    assert connection.commits == 0


def test_analytics_definitions_expose_hourly_utc_schedule():
    schedule = dagster_defs.defs.get_schedule_def("analytics_hourly_schedule")

    assert schedule.cron_schedule == "0 * * * *"
    assert schedule.execution_timezone == "UTC"
