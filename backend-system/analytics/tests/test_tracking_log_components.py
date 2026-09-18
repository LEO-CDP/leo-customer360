"""Unit tests for the extracted tracking-log analytics components."""

from io import BytesIO
from unittest.mock import MagicMock

import pytest

from source_analytics.config import AnalyticsSettings
from source_analytics.event_records import EventEnvelopeError, EventRecordService
from source_analytics.metrics import AnalyticsMetrics
from source_analytics.source_state import SourceStateStore
from source_analytics.tracking_log_service import TrackingLogAggregationService


class StateRedis:
    def __init__(self):
        self.states = {}
        self.locked_keys = set()
        self.hll = {}
        self.eval_results = []

    def set(self, key, value, nx=False, ex=None):
        assert nx is True
        if key in self.locked_keys:
            return False
        self.locked_keys.add(key)
        self.states.setdefault(key, {})["token"] = value
        self.states[key]["ttl"] = ex
        return True

    def hset(self, key, mapping):
        self.states.setdefault(key, {}).update(mapping)

    def hgetall(self, key):
        return self.states.get(key, {})

    def pfadd(self, key, *values):
        self.hll.setdefault(key, set()).update(values)
        return 1

    def pfcount(self, key):
        return len(self.hll.get(key, set()))

    def eval(self, script, _key_count, *args):
        if "EXPIRE" in script:
            return 1
        if "DEL" in script:
            self.locked_keys.discard(args[0])
            return 1
        return self.eval_results.pop(0) if self.eval_results else 1

    def scan_iter(self, match):
        prefix = match.removesuffix("*")
        return (key for key in self.states if key.startswith(prefix))

    def exists(self, key):
        return int(key in self.locked_keys)


def test_event_service_streams_fallback_timestamps_from_object_partition():
    service = EventRecordService("customer360")
    records = service.iter_normalized_event_records(
        BytesIO(
            b'{"event_id":"11111111-1111-1111-1111-111111111111",'
            b'"payload":{"event_name":"page_view","user_id":"User-1"}}\n'
        ),
        "events/2026-09-18-12/events.jsonl",
        "source-1",
        "tenant-1",
    )

    event = next(records)

    assert event["event_time"] == "2026-09-18T12:00:00+00:00"
    assert event["event_name"] == "page_view"
    assert event["payload"]["user_id"] == "User-1"


def test_event_service_rejects_unsupported_schema_version():
    service = EventRecordService("customer360")

    with pytest.raises(EventEnvelopeError, match="unsupported schema_version"):
        service.normalize_event_record(
            {
                "schema_version": 2,
                "event_id": "11111111-1111-1111-1111-111111111111",
                "event_time": "2026-09-18T12:00:00Z",
                "payload": {"event_name": "page_view"},
            },
            "source-1",
            "tenant-1",
        )


def test_event_service_rejects_records_without_a_valid_event_time():
    service = EventRecordService("customer360")

    with pytest.raises(EventEnvelopeError, match="event_time must be a valid"):
        service.normalize_event_record(
            {
                "event_id": "11111111-1111-1111-1111-111111111111",
                "payload": {"event_name": "page_view"},
            },
            "source-1",
            "tenant-1",
        )


def test_metrics_coerce_invalid_daily_values_and_worker_counters():
    assert AnalyticsMetrics.daily_stats({"2026-09-17": "12", "2026-09-18": "bad"}) == (
        2,
        12,
    )
    assert AnalyticsMetrics.aggregate_source_results(
        [
            {
                "skipped_running": None,
                "objects_processed": None,
                "events_added": "4",
            },
            {
                "skipped_running": True,
                "objects_processed": 1,
                "events_added": 2,
            },
        ]
    ) == {
        "sources_processed": 1,
        "sources_skipped_running": 1,
        "objects_processed": 1,
        "events_added": 6,
        "sources_total": 2,
    }


def test_source_state_store_tracks_lock_cursor_and_profiles():
    redis_client = StateRedis()
    settings = AnalyticsSettings.from_environment()
    state = SourceStateStore(redis_client, settings, lambda: "2026-09-18-12")

    token = state.acquire_source_lock("source-1", "run-1")
    assert token
    assert redis_client.hgetall(state.source_state_key("source-1"))["status"] == "running"

    state.save_source_cursor("source-1", "2026-09-18-12", "events/object.jsonl")
    state.add_profile_signatures("source-1", {"email:user@example.com"})
    assert state.get_source_cursor("source-1") == "events/object.jsonl"
    assert state.get_source_last_hour("source-1") == "2026-09-18-12"
    assert state.profile_count("source-1") == 1

    state.release_source_lock("source-1", token)
    assert state.get_source_statuses()[0]["status"] == "stale"


def test_tracking_service_uses_injected_source_loader():
    source_loader = MagicMock(return_value=[])
    connection = object()
    service = TrackingLogAggregationService(
        settings=AnalyticsSettings.from_environment(),
        s3_client=object(),
        redis_client=StateRedis(),
        db_connection=connection,
        source_loader=source_loader,
        database_connector=MagicMock(),
    )

    assert service.run(data_source_limit=7, lock_acquired=True) == {
        "sources_processed": 0,
        "sources_skipped_running": 0,
        "objects_processed": 0,
        "events_added": 0,
        "sources_total": 0,
    }
    source_loader.assert_called_once_with(connection, 7)


def test_tracking_service_rescans_active_receive_hour():
    settings = AnalyticsSettings.from_environment()
    service = TrackingLogAggregationService(
        settings=settings,
        s3_client=object(),
        redis_client=StateRedis(),
        db_connection=object(),
        source_loader=MagicMock(return_value=[]),
        database_connector=MagicMock(),
        clock=lambda: "2026-09-18-12",
    )
    service.state.save_source_cursor(
        "source-1",
        "2026-09-18-12",
        "events/2026-09-18-12/old-batch.jsonl.gz",
    )

    assert service._source_start_after("source-1") is None