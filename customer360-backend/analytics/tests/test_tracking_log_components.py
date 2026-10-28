"""Unit tests for the extracted tracking-log analytics components."""

from io import BytesIO
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from source_analytics.config import AnalyticsSettings
from source_analytics.event_records import EventEnvelopeError, EventRecordService
from source_analytics.metrics import AnalyticsMetrics
from source_analytics.source_state import SourceStateStore
from source_analytics.tracking_log_service import TrackingLogAggregationService


class StateRedis:
    def __init__(self):
        self.states = {}
        self.values = {}
        self.locked_keys = set()
        self.hll = {}
        self.eval_results = []

    def set(self, key, value, nx=False, ex=None):
        if not nx:
            self.values[key] = value
            return True
        if key in self.locked_keys:
            return False
        self.locked_keys.add(key)
        self.states.setdefault(key, {})["token"] = value
        self.states[key]["ttl"] = ex
        return True

    def get(self, key):
        return self.values.get(key)

    def hset(self, key, mapping):
        self.states.setdefault(key, {}).update(mapping)

    def hincrby(self, key, field, increment):
        state = self.states.setdefault(key, {})
        state[field] = str(int(state.get(field, 0)) + increment)

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


def test_event_service_extracts_nested_profile_and_event_data_from_tracking_envelope():
    service = EventRecordService("customer360")
    event = service.normalize_event_record(
        {
            "schema_version": 1,
            "event_id": "11111111-1111-1111-1111-111111111111",
            "event_time": "2026-09-29T16:47:52.584Z",
            "event_name": "submit-contact",
            "identity": {
                "anonymous_id": "anonymous-1",
                "session_id": "session-1",
                "device_fingerprint": "fingerprint-1",
            },
            "payload": {
                "event_name": "submit-contact",
                "anonymous_id": "anonymous-1",
                "device_fingerprint": "fingerprint-1",
                "event_data": {
                    "form_id": "lottery-alert",
                    "email": "trieu@leocdp.com",
                },
                "profile_data": {
                    "user_id": "demo-subscriber",
                    "email": "trieu@leocdp.com",
                },
            },
        },
        "source-1",
        "tenant-1",
    )

    raw_profile = service.extract_raw_profile(event)

    assert event["email"] == "trieu@leocdp.com"
    assert event["anonymous_id"] == "anonymous-1"
    assert event["device_fingerprint"] == "fingerprint-1"
    assert raw_profile["email"] == "trieu@leocdp.com"
    assert raw_profile["anonymous_id"] == "anonymous-1"
    assert raw_profile["device_fingerprint"] == "fingerprint-1"


def test_event_service_normalizes_campaign_experiment_attribution():
    service = EventRecordService("customer360")
    event = service.normalize_event_record(
        {
            "event_id": "11111111-1111-1111-1111-111111111111",
            "event_time": "2026-09-30T12:00:00Z",
            "payload": {
                "event_name": "purchase",
                "campaign_id": "22222222-2222-2222-2222-222222222222",
                "experiment_variant_id": "33333333-3333-3333-3333-333333333333",
                "event_value": 125.5,
                "user_id": "user-1",
            },
        },
        "source-1",
        "tenant-1",
    )

    assert event["campaign_id"] == "22222222-2222-2222-2222-222222222222"
    assert event["experiment_variant_id"] == "33333333-3333-3333-3333-333333333333"
    assert service.extract_raw_profile(event)["event_payload"]["experiment_variant_id"] == event["experiment_variant_id"]


def test_validated_event_name_uses_catalog_names():
    catalog = frozenset({"page-view", "purchase"})
    expected = {
        "page_view": "page-view",
        "purchase": "purchase",
        "ui_click": None,
    }

    assert all(
        EventRecordService.validated_event_name({"event_name": name}, catalog) == result
        for name, result in expected.items()
    )


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


def test_event_service_normalizes_legacy_non_uuid_event_id():
    service = EventRecordService("customer360")
    record = {
        "event_id": "event-1",
        "event_time": "2026-09-18T12:00:00Z",
        "payload": {"event_name": "page_view", "user_id": "User-1"},
    }

    first = service.normalize_event_record(record, "source-1", "tenant-1")
    second = service.normalize_event_record(record, "source-1", "tenant-1")

    assert UUID(first["event_id"])
    assert first["event_id"] == second["event_id"]
    assert first["event_id"] != "event-1"


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


def test_source_state_accumulates_deduplicated_profile_analytics():
    redis_client = StateRedis()
    settings = AnalyticsSettings.from_environment()
    state = SourceStateStore(redis_client, settings, lambda: "2026-09-18-12")

    first = state.record_profile_event_analytics(
        "source-1",
        "raw-1",
        "event-1",
        event_name="page-view",
    )
    duplicate = state.record_profile_event_analytics(
        "source-1",
        "raw-1",
        "event-1",
        event_name="page-view",
    )
    second = state.record_profile_event_analytics(
        "source-1",
        "raw-1",
        "event-2",
        event_name="purchase",
    )

    assert first["total_tracked_events"] == 1
    assert duplicate == first
    assert second == {
        "page-view": 1,
        "purchase": 1,
        "total_tracked_events": 2,
    }


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