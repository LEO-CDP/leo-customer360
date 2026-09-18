"""Contract tests for tenant-scoped S3/MinIO event queries."""

import gzip
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

from botocore.exceptions import ClientError
import pytest

from core.config import Settings
from core.repositories.event_query_repository import EventDataSourceError, EventQueryError, EventQueryRepository


TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
SOURCE_ID = UUID("22222222-2222-2222-2222-222222222222")


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeDb:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [(SOURCE_ID,)]

    def execute(self, _statement):
        return FakeResult(self.rows)


class FakePaginator:
    def __init__(self, calls, key):
        self.calls = calls
        self.key = key

    def paginate(self, **kwargs):
        self.calls.append(kwargs)
        prefix = kwargs.get("Prefix")
        if prefix and not self.key.startswith(prefix):
            return [{"Contents": []}]
        return [{"Contents": [{"Key": self.key}]}]


class FakeS3:
    def __init__(self, body, key):
        self.body = body
        self.key = key
        self.list_calls = []
        self.get_calls = []

    def get_paginator(self, _name):
        return FakePaginator(self.list_calls, self.key)

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        return {"Body": BytesIO(self.body)}


class MissingBucketS3(FakeS3):
    def get_paginator(self, _name):
        class MissingPaginator:
            def paginate(self, **_kwargs):
                raise ClientError(
                    {"Error": {"Code": "NoSuchBucket"}},
                    "ListObjectsV2",
                )

        return MissingPaginator()


def _gzip_envelopes(*envelopes):
    raw = b"\n".join(
        json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        for envelope in envelopes
    ) + b"\n"
    return gzip.compress(raw)


def test_query_reads_current_tracking_envelope_with_polars_and_tenant_source_scope():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    object_key = f"events/{now.strftime('%Y-%m-%d-%H')}/batch.jsonl.gz"
    body = _gzip_envelopes(
        {
            "schema_version": 1,
            "event_id": "event-old",
            "data_source_id": str(SOURCE_ID),
            "event_time": (now - timedelta(hours=2)).isoformat(),
            "received_at": now.isoformat(),
            "event_name": "page_view",
            "event_category": "GENERAL",
            "identity": {"user_id": "user-1"},
            "payload": {"event_name": "page_view", "user_id": "user-1"},
        },
        {
            "schema_version": 1,
            "event_id": "event-new",
            "data_source_id": str(SOURCE_ID),
            "event_time": (now - timedelta(minutes=5)).isoformat(),
            "received_at": now.isoformat(),
            "event_name": "purchase",
            "event_category": "COMMERCE",
            "identity": {"user_id": "user-1"},
            "payload": {
                "event_name": "purchase",
                "user_id": "user-1",
                "channel": "web",
            },
        },
    )
    s3 = FakeS3(body, object_key)
    settings = Settings(
        event_s3_bucket=None,
        event_s3_prefix="events",
        event_query_max_days=90,
    )
    repository = EventQueryRepository(settings, s3_client=s3)

    rows = repository.query(
        FakeDb(),
        TENANT_ID,
        event_time_from=now - timedelta(hours=1),
        days=90,
        limit=1,
        event_category="COMMERCE",
    )

    assert [row["event_id"] for row in rows] == ["event-new"]
    assert rows[0]["tenant_id"] == str(TENANT_ID)
    assert rows[0]["event_payload"]["channel"] == "web"
    assert s3.list_calls
    assert s3.list_calls[0]["Bucket"] == f"data-tracking-{SOURCE_ID}"
    assert s3.list_calls[0]["Prefix"].startswith("events/")
    assert s3.get_calls == [
        {"Bucket": f"data-tracking-{SOURCE_ID}", "Key": object_key}
    ]


def test_query_daily_totals_groups_hourly_events_by_utc_day():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    object_key = f"events/{now.strftime('%Y-%m-%d-%H')}/batch.jsonl.gz"
    body = _gzip_envelopes(
        {
            "schema_version": 1,
            "event_id": "event-hour-1",
            "event_time": (now - timedelta(minutes=5)).isoformat(),
            "event_name": "page_view",
            "payload": {"event_name": "page_view"},
        },
        {
            "schema_version": 1,
            "event_id": "event-hour-2",
            "event_time": (now - timedelta(minutes=15)).isoformat(),
            "event_name": "purchase",
            "payload": {"event_name": "purchase"},
        },
    )
    repository = EventQueryRepository(
        Settings(event_s3_prefix="events", event_query_max_days=90),
        s3_client=FakeS3(body, object_key),
    )

    totals = repository.query_daily_totals(
        FakeDb(),
        TENANT_ID,
        event_time_from=now - timedelta(hours=1),
        days=90,
    )

    assert len(totals) == 1
    assert totals[0]["total"] == 2
    assert totals[0]["day"].isoformat() == now.strftime("%Y-%m-%d")


def test_query_channel_totals_groups_complete_event_window():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    object_key = f"events/{now.strftime('%Y-%m-%d-%H')}/batch.jsonl.gz"
    body = _gzip_envelopes(
        {
            "schema_version": 1,
            "event_id": "event-channel-1",
            "event_time": (now - timedelta(minutes=5)).isoformat(),
            "payload": {"event_name": "page_view", "channel": "web"},
        },
        {
            "schema_version": 1,
            "event_id": "event-channel-2",
            "event_time": (now - timedelta(minutes=10)).isoformat(),
            "payload": {"event_name": "app_open", "channel": "mobile_app"},
        },
        {
            "schema_version": 1,
            "event_id": "event-channel-3",
            "event_time": (now - timedelta(minutes=15)).isoformat(),
            "payload": {"event_name": "purchase", "channel": "web"},
        },
    )
    repository = EventQueryRepository(
        Settings(event_s3_prefix="events", event_query_max_days=90),
        s3_client=FakeS3(body, object_key),
    )

    totals = repository.query_channel_totals(
        FakeDb(),
        TENANT_ID,
        event_time_from=now - timedelta(hours=1),
        days=30,
    )

    assert totals == [
        {"channel": "web", "total": 2},
        {"channel": "mobile_app", "total": 1},
    ]
    s3 = repository.s3
    assert len(s3.list_calls) == 1
    assert s3.list_calls[0]["Prefix"] == f"events/{now.date().isoformat()}-"


def test_query_skips_active_sources_without_provisioned_buckets():
    repository = EventQueryRepository(
        Settings(event_s3_prefix="events"),
        s3_client=MissingBucketS3(b"", "events/2026-09-15-14/missing.jsonl.gz"),
    )

    rows = repository.query(
        FakeDb(),
        TENANT_ID,
        event_time_from=datetime.now(timezone.utc) - timedelta(days=1),
        days=1,
        limit=1000,
    )

    assert rows == []


def test_query_rejects_requested_source_without_active_tenant_row_before_s3():
    repository = EventQueryRepository(
        Settings(event_s3_prefix="events"),
        s3_client=MissingBucketS3(b"", "events/missing.jsonl.gz"),
    )

    with pytest.raises(EventDataSourceError, match="invalid, inactive"):
        repository.query(
            FakeDb(rows=[]),
            TENANT_ID,
            data_source_id=SOURCE_ID,
            event_time_from=datetime.now(timezone.utc) - timedelta(days=1),
            days=1,
            limit=1000,
        )


def test_query_rejects_invalid_source_id_from_postgresql_before_s3():
    repository = EventQueryRepository(
        Settings(event_s3_prefix="events"),
        s3_client=MissingBucketS3(b"", "events/missing.jsonl.gz"),
    )

    with pytest.raises(EventQueryError, match="Invalid data source ID"):
        repository.query(
            FakeDb(rows=[("not-a-uuid",)]),
            TENANT_ID,
            event_time_from=datetime.now(timezone.utc) - timedelta(days=1),
            days=1,
            limit=1000,
        )
