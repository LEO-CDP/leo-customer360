"""Tests for the durable Redis Stream tracking handoff."""

import base64
import gzip
import json
import os
import socket
from datetime import datetime, timezone
from uuid import UUID

import pytest
import redis

from core.redis_queue import (
    RedisStreamTrackingStorage,
    TrackingQueueUnavailableError,
)
from core.storage import StoredTrackingLog

SOURCE_ID = UUID("11111111-1111-1111-1111-111111111111")


class FakeRedisStream:
    def __init__(self, error=None):
        self.error = error
        self.added = []
        self.acknowledged = []
        self.deleted = []

    def xadd(self, stream_name, fields, **kwargs):
        if self.error:
            raise self.error
        self.added.append((stream_name, fields, kwargs))
        return "1710000000000-0"

    def eval(
        self,
        _script,
        _key_count,
        stream_name,
        _idempotency_key,
        payload,
        _max_length,
        _ttl_seconds,
    ):
        if self.error:
            raise self.error
        self.added.append((stream_name, {"payload": payload}, {}))
        return "1710000000000-0"

    def xack(self, stream_name, group_name, message_id):
        self.acknowledged.append((stream_name, group_name, message_id))

    def xdel(self, stream_name, message_id):
        self.deleted.append((stream_name, message_id))


class FakeS3Storage:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def store_prebuilt_tracking_object(self, **kwargs):
        if self.error:
            raise self.error
        self.calls.append(kwargs)
        return StoredTrackingLog(
            data_source_id=kwargs["data_source_id"],
            bucket=kwargs["bucket"],
            object_key=kwargs["object_key"],
            event_count=kwargs["event_count"],
            received_at=kwargs["received_at"],
        )


def _publisher(redis_client, storage):
    publisher = object.__new__(RedisStreamTrackingStorage)
    publisher.redis = redis_client
    publisher.storage = storage
    publisher.stream_name = "tracking-events"
    publisher.consumer_group = "s3-writers"
    publisher.max_stream_length = 1000
    return publisher


def test_redis_stream_publish_does_not_call_s3_and_preserves_nested_json():
    broker = FakeRedisStream()
    storage = FakeS3Storage()
    publisher = _publisher(broker, storage)

    stored = publisher.store_tracking_logs(
        SOURCE_ID,
        [{"event": "purchase", "properties": {"items": ["sku-1"]}}],
        datetime(2026, 9, 10, 8, 30, tzinfo=timezone.utc),
    )

    assert stored.queue_message_id == "1710000000000-0"
    assert not storage.calls
    payload = json.loads(broker.added[0][1]["payload"])
    body = gzip.decompress(base64.b64decode(payload["body"]))
    assert json.loads(body.strip())["payload"]["properties"]["items"] == ["sku-1"]


def test_redis_stream_worker_acknowledges_only_after_s3_write():
    broker = FakeRedisStream()
    storage = FakeS3Storage()
    publisher = _publisher(broker, storage)
    publisher.store_tracking_logs(
        SOURCE_ID,
        [{"event": "page_view", "device_id": "device-1"}],
        datetime(2026, 9, 10, 8, 31, tzinfo=timezone.utc),
    )
    fields = broker.added[0][1]

    publisher._process_messages([("1710000000000-0", fields)])

    assert len(storage.calls) == 1
    assert broker.acknowledged == [("tracking-events", "s3-writers", "1710000000000-0")]
    assert broker.deleted == [("tracking-events", "1710000000000-0")]


def test_redis_stream_worker_leaves_failed_message_unacknowledged():
    broker = FakeRedisStream()
    storage = FakeS3Storage(error=RuntimeError("s3 unavailable"))
    publisher = _publisher(broker, storage)
    publisher.store_tracking_logs(
        SOURCE_ID,
        [{"event": "page_view", "session_id": "session-1"}],
        datetime(2026, 9, 10, 8, 32, tzinfo=timezone.utc),
    )

    assert not publisher._process_messages(
        [("1710000000000-0", broker.added[0][1])]
    )
    assert not broker.acknowledged


def test_redis_stream_publish_reports_broker_failure():
    publisher = _publisher(FakeRedisStream(error=redis.RedisError("redis down")), FakeS3Storage())

    with pytest.raises(TrackingQueueUnavailableError):
        publisher.store_tracking_logs(
            SOURCE_ID,
            [{"event": "page_view", "user_id": "user-1"}],
            datetime(2026, 9, 10, 8, 33, tzinfo=timezone.utc),
        )

class IdleRedisStream(FakeRedisStream):
    """Broker that keeps the worker thread looping without any work to do."""

    def xgroup_create(self, *_args, **_kwargs):
        return True

    def xreadgroup(self, *_args, **_kwargs):
        return []

    def xautoclaim(self, *_args, **_kwargs):
        return ("0-0", [])


def _worker(**kwargs):
    return RedisStreamTrackingStorage(
        storage=FakeS3Storage(),
        redis_client=IdleRedisStream(),
        stream_name="tracking-events",
        consumer_group="s3-writers",
        max_stream_length=1000,
        flush_batch_size=10,
        block_ms=1,
        claim_idle_ms=60000,
        retry_seconds=0.1,
        **kwargs,
    )


def test_consumer_name_identifies_the_pod_not_the_object():
    """Replicas must be told apart by hostname+pid, never by id(self).

    Identical containers allocate objects at the same address, so an id(self)
    based name collides across pods and they claim each other's pending
    messages. Two instances in ONE process therefore share a name (same pod),
    while a different hostname must produce a different one.
    """
    first, second = _worker(), _worker()
    try:
        assert first.consumer_name == second.consumer_name
        assert socket.gethostname() in first.consumer_name
        assert str(os.getpid()) in first.consumer_name
    finally:
        first.close()
        second.close()


def test_consumer_name_changes_with_the_pod_hostname(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "event-api-abc123")
    worker = _worker()
    try:
        assert "event-api-abc123" in worker.consumer_name
    finally:
        worker.close()


def test_explicit_consumer_name_is_respected():
    worker = _worker(consumer_name="pinned-worker")
    try:
        assert worker.consumer_name == "pinned-worker"
    finally:
        worker.close()
