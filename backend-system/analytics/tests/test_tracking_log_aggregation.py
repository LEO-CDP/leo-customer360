"""Tests for tracking-log aggregation and its Dagster wrapper."""

from io import BytesIO
import re
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
        prefix = kwargs.get("Prefix")
        if not prefix:
            return self.pages
        return [
            {
                **page,
                "Contents": [
                    item
                    for item in page.get("Contents", [])
                    if str(item.get("Key", "")).startswith(prefix)
                ],
            }
            for page in self.pages
        ]


class FakeS3:
    def __init__(self, objects):
        self.objects = {
            key: value.getvalue() if hasattr(value, "getvalue") else value
            for key, value in objects.items()
        }
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
        payload = self.objects[kwargs["Key"]]
        body = BytesIO(payload) if isinstance(payload, (bytes, bytearray)) else payload
        return {"Body": body}


class FakeRedis:
    def __init__(self, results):
        self.results = iter(results)
        self.eval_calls = []
        self.states = {}
        self.hll = {}
        self.locked = False
        self.locked_keys = set()

    def set(self, key, _value, nx=False, ex=None):
        assert nx is True
        assert ex == aggregation.LOCK_TTL_SECONDS
        if self.locked or key in self.locked_keys:
            return False
        self.locked_keys.add(key)
        return True

    def hset(self, key, mapping):
        self.states.setdefault(key, {}).update(mapping)

    def hgetall(self, key):
        return self.states.get(key, {})

    def pfadd(self, key, *values):
        self.hll.setdefault(key, set()).update(str(value) for value in values)
        return 1

    def pfcount(self, key):
        return len(self.hll.get(key, set()))

    def exists(self, key):
        return int(self.locked or key in self.locked_keys)

    def eval(self, *args):
        self.eval_calls.append(args)
        script = args[0]
        if "EXPIRE" in script:
            return 1
        if "DEL" in script:
            self.locked_keys.discard(args[2])
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
    assert cursor.execute_calls[2][1] == ("tenant-b", 3)
    assert cursor.execute_calls[3][1] == ("tenant-a",)
    assert "status = 1" in cursor.execute_calls[2][0]


def test_fetch_data_sources_without_limit_reads_all_active_sources():
    cursor = FakeCursor(
        [
            [("tenant-a",)],
            [("source-1", "tenant-a"), ("source-2", "tenant-a")],
        ]
    )
    connection = FakeConnection(cursor)

    result = aggregation.fetch_data_sources(connection, limit=0)

    assert result == [("source-1", "tenant-a"), ("source-2", "tenant-a")]
    assert cursor.execute_calls[2][1] == ("tenant-a",)
    assert "LIMIT" not in cursor.execute_calls[2][0]


def test_count_jsonl_records_ignores_blank_lines_and_requires_objects():
    body = BytesIO(b'{"event": "page_view"}\n\n{"event": "purchase"}\n')

    assert aggregation.count_jsonl_records(body, "hour/events.jsonl") == 2


def test_current_system_gmt_hour_uses_required_format():
    value = aggregation.current_system_gmt_hour()

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{2}", value)


def test_s3_json_cache_key_uses_json_path():
    key = aggregation.s3_json_cache_key(
        "data-tracking-source-1",
        "2026-08-25-08/first.jsonl",
    )

    assert key == "s3://data-tracking-source-1/2026-08-25-08/first.jsonl"


def test_iter_hourly_objects_uses_last_processed_object_as_start_after():
    s3 = FakeS3({})

    list(
        aggregation.iter_hourly_objects(
            s3,
            "data-tracking-source-1",
            start_after="2026-08-25-08/first.jsonl",
        )
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
    redis_client.states["analytics:data-source-state:source-1"] = {
        "last_processed_object": "2026-08-24-23/old.jsonl",
    }
    monkeypatch.setattr(
        aggregation,
        "fetch_data_sources",
        MagicMock(return_value=[("source-1", "tenant-1")]),
    )
    monkeypatch.setattr(aggregation, "current_system_gmt_hour", lambda: "2026-08-25-08")

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
        "sources_total": 1,
    }
    assert len(s3.get_calls) == 1
    increment_call = next(call for call in redis_client.eval_calls if "HINCRBY" in call[0])
    assert increment_call[3] == "s3://data-tracking-source-1/2026-08-25-08/first.jsonl"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{2}", str(increment_call[4]))
    assert increment_call[5:8] == (
        "tracked-event",
        "2",
        str(aggregation.PROCESSED_OBJECT_TTL_SECONDS),
    )
    assert "total_tracked_event" in cursor.execute_calls[-1][0]
    assert "avg_daily_event" in cursor.execute_calls[-1][0]
    assert "avg_events_per_profile" in cursor.execute_calls[-1][0]
    assert cursor.execute_calls[-1][1] == (2, 2, 0.0, "source-1", "tenant-1")
    assert s3.paginate_calls == [
        {
            "Bucket": "data-tracking-source-1",
            "Prefix": "2026-08-25-08/",
        },
    ]
    assert redis_client.states["analytics:data-source-state:source-1"][
        "last_processed_hour"
    ] == "2026-08-25-08"
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
        "sources_total": 2,
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
    redis_client.locked_keys.add(aggregation._source_lock_key("source-1"))
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
    assert summary["sources_total"] == 1
    assert connection.commits == 0


def test_process_tracking_logs_stops_at_object_batch_limit(monkeypatch):
    cursor = FakeCursor([[]], rowcount=1)
    connection = FakeConnection(cursor)
    s3 = FakeS3(
        {
            "2026-08-25-08/first.jsonl": BytesIO(b'{"event": "page_view"}\n'),
            "2026-08-25-09/second.jsonl": BytesIO(b'{"event": "purchase"}\n'),
        }
    )
    redis_client = FakeRedis([1])
    monkeypatch.setattr(aggregation, "OBJECT_BATCH_SIZE", 1)
    monkeypatch.setattr(
        aggregation,
        "fetch_data_sources",
        MagicMock(return_value=[("source-1", "tenant-1")]),
    )
    monkeypatch.setattr(aggregation, "current_system_gmt_hour", lambda: "2026-08-25-08")

    summary = aggregation.process_tracking_logs(
        s3_client=s3,
        redis_client=redis_client,
        db_connection=connection,
    )

    assert summary["objects_processed"] == 1
    assert summary["events_added"] == 1
    assert len(s3.get_calls) == 1


def test_analytics_definitions_expose_three_minute_gmt_schedule():
    schedule = dagster_defs.defs.get_schedule_def("analytics_hourly_schedule")

    assert schedule.cron_schedule == "*/3 * * * *"
    assert schedule.execution_timezone == "GMT"


def test_summarize_bucket_metrics_counts_profiles_and_daily_average():
    s3 = FakeS3(
        {
            "2026-08-25-08/first.jsonl": BytesIO(
                b'{"event":{"external_customer_id":"cust-1"}}\n'
                b'{"event":{"profile_identities":{"email":"u2@example.com"}}}\n'
            ),
            "2026-08-25-09/second.jsonl": BytesIO(
                b'{"event":{"external_customer_id":"cust-1"}}\n'
            ),
        }
    )

    total, avg_daily, avg_per_profile = aggregation.summarize_bucket_metrics(
        s3, "data-tracking-source-1"
    )

    assert total == 3
    assert avg_daily == 3
    assert avg_per_profile == 1.5


def test_count_records_and_signatures_collects_identity_keys():
    body = BytesIO(
        b'{"event":{"external_customer_id":"C-1"}}\n'
        b'{"event":{"profile_identities":{"email":"A@B.COM"}}}\n'
        b'{"event":{"session_id":"S-1"}}\n'
    )

    count, signatures = aggregation.count_records_and_signatures(body, "hour/events.jsonl")

    assert count == 3
    assert "external_customer_id:c-1" in signatures
    assert "email:a@b.com" in signatures
    assert "session_id:s-1" in signatures


def test_aggregate_source_results_rolls_up_worker_metrics():
    summary = aggregation._aggregate_source_results(
        [
            {
                "data_source_id": "s1",
                "tenant_id": "t1",
                "skipped_running": False,
                "objects_processed": 10,
                "events_added": 100,
            },
            {
                "data_source_id": "s2",
                "tenant_id": "t1",
                "skipped_running": True,
                "objects_processed": 0,
                "events_added": 0,
            },
            {
                "data_source_id": "s3",
                "tenant_id": "t2",
                "skipped_running": False,
                "objects_processed": 5,
                "events_added": 60,
            },
        ]
    )

    assert summary == {
        "sources_processed": 2,
        "sources_skipped_running": 1,
        "objects_processed": 15,
        "events_added": 160,
        "sources_total": 3,
    }


def test_aggregate_source_results_with_pandas_engine():
    summary = aggregation._aggregate_source_results(
        [
            {
                "data_source_id": "s1",
                "tenant_id": "t1",
                "skipped_running": False,
                "objects_processed": 2,
                "events_added": 20,
            },
            {
                "data_source_id": "s2",
                "tenant_id": "t1",
                "skipped_running": True,
                "objects_processed": 0,
                "events_added": 0,
            },
        ],
        engine="pandas",
    )

    assert summary == {
        "sources_processed": 1,
        "sources_skipped_running": 1,
        "objects_processed": 2,
        "events_added": 20,
        "sources_total": 2,
    }


def test_aggregate_source_results_with_polars_engine():
    summary = aggregation._aggregate_source_results(
        [
            {
                "data_source_id": "s1",
                "tenant_id": "t1",
                "skipped_running": False,
                "objects_processed": 3,
                "events_added": 30,
            },
            {
                "data_source_id": "s2",
                "tenant_id": "t1",
                "skipped_running": False,
                "objects_processed": 4,
                "events_added": 50,
            },
        ],
        engine="polars",
    )

    assert summary == {
        "sources_processed": 2,
        "sources_skipped_running": 0,
        "objects_processed": 7,
        "events_added": 80,
        "sources_total": 2,
    }


def test_get_daily_stats_with_pandas_and_polars_engines():
    redis_client = FakeRedis([])
    redis_client.hset(
        "analytics:data-source-daily:source-1",
        {
            "2026-09-05": "100",
            "2026-09-06": "150",
        },
    )

    pandas_days, pandas_total = aggregation._get_daily_stats(
        redis_client, "source-1", engine="pandas"
    )
    polars_days, polars_total = aggregation._get_daily_stats(
        redis_client, "source-1", engine="polars"
    )

    assert (pandas_days, pandas_total) == (2, 250)
    assert (polars_days, polars_total) == (2, 250)
