"""Tests for the in-process buffered tracking storage queue."""

import time
from datetime import datetime, timezone
from uuid import UUID

from core.buffered_storage import BufferedTrackingStorage
from core.storage import StoredTrackingLog

SOURCE_ID = UUID("11111111-1111-1111-1111-111111111111")


class FakeS3Storage:
    def __init__(self):
        self.calls = []

    def store_prebuilt_tracking_object(
        self,
        data_source_id,
        bucket,
        object_key,
        body,
        event_count,
        received_at,
    ):
        self.calls.append(
            {
                "data_source_id": data_source_id,
                "bucket": bucket,
                "object_key": object_key,
                "body": body,
                "event_count": event_count,
                "received_at": received_at,
            }
        )
        return StoredTrackingLog(
            data_source_id=data_source_id,
            bucket=bucket,
            object_key=object_key,
            event_count=event_count,
            received_at=received_at,
        )


def _wait_until(predicate, timeout_seconds=2.5):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_buffered_storage_flushes_after_interval():
    fake_storage = FakeS3Storage()
    buffered = BufferedTrackingStorage(
        storage=fake_storage,
        flush_interval_seconds=1,
        max_queue_size=16,
        flush_batch_size=8,
    )

    try:
        received_at = datetime(2026, 9, 9, 4, 0, tzinfo=timezone.utc)
        stored = buffered.store_tracking_logs(SOURCE_ID, [{"event": "page_view"}], received_at)

        assert stored.bucket == f"data-tracking-{SOURCE_ID}"
        assert stored.object_key.startswith("2026-09-09-04/")
        assert len(fake_storage.calls) == 0

        assert _wait_until(lambda: len(fake_storage.calls) == 1)
        assert fake_storage.calls[0]["bucket"] == stored.bucket
        assert fake_storage.calls[0]["object_key"] == stored.object_key
        assert fake_storage.calls[0]["event_count"] == 1
    finally:
        buffered.close()


def test_buffered_storage_close_flushes_pending_objects():
    fake_storage = FakeS3Storage()
    buffered = BufferedTrackingStorage(
        storage=fake_storage,
        flush_interval_seconds=60,
        max_queue_size=16,
        flush_batch_size=100,
    )

    received_at = datetime(2026, 9, 9, 4, 5, tzinfo=timezone.utc)
    buffered.store_tracking_logs(SOURCE_ID, [{"event": "a"}], received_at)
    buffered.store_tracking_logs(SOURCE_ID, [{"event": "b"}], received_at)

    assert len(fake_storage.calls) == 0
    buffered.close(timeout_seconds=10)

    assert len(fake_storage.calls) == 2
    assert fake_storage.calls[0]["event_count"] == 1
    assert fake_storage.calls[1]["event_count"] == 1
