"""Focused tests for tracking ingestion and object-key partitioning."""

import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app import app
from core.config import Settings
from core.redis_cache import EventStreamPublisher, RateLimitDecision, TrackingRequestProtection
from core.routers.tracking import get_protection, get_tracking_service
from core.service import TrackingLogService, _collect_sessions
from core.storage import StoredTrackingLog, build_tracking_object


SOURCE_ID = UUID("11111111-1111-1111-1111-111111111111")


class FakeStorage:
    def __init__(self):
        self.calls = []

    def store_tracking_logs(self, data_source_id, events, received_at):
        self.calls.append((data_source_id, events, received_at))
        return StoredTrackingLog(
            data_source_id=data_source_id,
            bucket=f"data-tracking-{data_source_id}",
            object_key="2026-08-25-14/batch.jsonl",
            event_count=len(events),
            received_at=received_at,
        )


class FakeSessionCache:
    def __init__(self):
        self.calls = []

    def touch_sessions(self, data_source_id, sessions, seen_at):
        self.calls.append((data_source_id, sessions, seen_at))
        return len(sessions)


class FakeRedis:
    def __init__(self, count=1, error=None):
        self.count = count
        self.error = error

    def eval(self, *_args):
        if self.error:
            raise self.error
        return self.count


class FakeProtection:
    def __init__(self, bot=False, decision=None):
        self.bot = bot
        self.decision = decision or RateLimitDecision(allowed=True)
        self.session_cache = FakeSessionCache()

    def is_bot(self, _user_agent):
        return self.bot

    def allow_request(self, _request):
        return self.decision


class FakeStreamPipeline:
    def __init__(self, error=None):
        self.error = error
        self.adds = []

    def xadd(self, key, fields, maxlen=None, approximate=None):
        self.adds.append((key, fields, maxlen, approximate))

    def execute(self):
        if self.error:
            raise self.error
        return [b"1-0"] * len(self.adds)


class FakeStreamRedis:
    def __init__(self, error=None):
        self.pipeline_obj = FakeStreamPipeline(error=error)

    def pipeline(self, transaction=True):
        return self.pipeline_obj


class CapturingStream:
    def __init__(self):
        self.calls = []

    def publish(self, data_source_id, events, received_at):
        self.calls.append((data_source_id, list(events), received_at))
        return len(events)


def test_build_tracking_object_uses_utc_hour_folder_and_ndjson():
    received_at = datetime(2026, 8, 25, 21, 5, tzinfo=timezone.utc)

    bucket, key, body = build_tracking_object(SOURCE_ID, [{"event": "page_view"}], received_at)

    assert bucket == f"data-tracking-{SOURCE_ID}"
    assert key.startswith("2026-08-25-21/")
    assert key.endswith(".jsonl")
    assert json.loads(body.decode().strip())["event"] == {"event": "page_view"}


def test_ingest_tracking_logs_returns_object_location():
    fake_storage = FakeStorage()
    fake_cache = FakeSessionCache()
    app.dependency_overrides[get_tracking_service] = lambda: TrackingLogService(fake_storage, fake_cache)
    try:
        response = TestClient(app).post(
            "/api/v1/tracking/logs",
            json={
                "data_source_id": str(SOURCE_ID),
                "session_id": "session-123",
                "user_id": "user-456",
                "events": [{"event": "page_view"}],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["bucket"] == f"data-tracking-{SOURCE_ID}"
    assert response.json()["event_count"] == 1
    assert response.json()["cached_session_count"] == 1
    assert fake_storage.calls[0][0] == SOURCE_ID
    assert fake_storage.calls[0][1][0]["session_id"] == "session-123"
    assert fake_storage.calls[0][1][0]["user_id"] == "user-456"
    assert fake_cache.calls[0][1] == {"session-123": (1, "user-456")}


def test_collect_sessions_aggregates_event_sessions_without_payloads():
    sessions = _collect_sessions(
        [
            {"session_id": "s-1"},
            {"session_id": "s-1", "user_id": "u-1"},
            {"session_id": "s-2"},
        ],
        session_id=None,
        user_id=None,
    )

    assert sessions == {"s-1": (2, "u-1"), "s-2": (1, None)}


def test_googlebot_is_filtered_by_user_agent():
    protection = TrackingRequestProtection(
        Settings(tracking_bot_filter_enabled=True),
        client=FakeRedis(),
    )

    assert protection.is_bot("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)")
    assert not protection.is_bot("Mozilla/5.0 Chrome/120.0")


def test_rate_limiter_rejects_after_limit_and_supports_fail_open():
    settings = Settings(
        tracking_rate_limit_requests=1,
        tracking_rate_limit_window_seconds=30,
        tracking_rate_limit_fail_open=False,
    )
    request = type("Request", (), {"client": type("Client", (), {"host": "127.0.0.1"})()})()

    denied = TrackingRequestProtection(settings, client=FakeRedis(count=2)).allow_request(request)
    assert denied == RateLimitDecision(allowed=False, retry_after_seconds=30)

    fail_open = TrackingRequestProtection(
        Settings(tracking_rate_limit_fail_open=True),
        client=FakeRedis(error=RedisError("redis down")),
    ).allow_request(request)
    assert fail_open.allowed


def test_bot_request_is_acknowledged_without_storage_or_rate_limit():
    fake_storage = FakeStorage()
    fake_protection = FakeProtection(bot=True)
    app.dependency_overrides[get_tracking_service] = lambda: TrackingLogService(
        fake_storage, FakeSessionCache()
    )
    app.dependency_overrides[get_protection] = lambda: fake_protection
    try:
        response = TestClient(app).post(
            "/api/v1/tracking/logs",
            headers={"User-Agent": "Googlebot/2.1"},
            json={"data_source_id": str(SOURCE_ID), "events": [{"event": "page_view"}]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["filtered"] is True
    assert not fake_storage.calls


def test_rate_limited_request_returns_retry_after_header():
    fake_storage = FakeStorage()
    fake_protection = FakeProtection(
        decision=RateLimitDecision(allowed=False, retry_after_seconds=17)
    )
    app.dependency_overrides[get_tracking_service] = lambda: TrackingLogService(
        fake_storage, FakeSessionCache()
    )
    app.dependency_overrides[get_protection] = lambda: fake_protection
    try:
        response = TestClient(app).post(
            "/api/v1/tracking/logs",
            json={"data_source_id": str(SOURCE_ID), "events": [{"event": "page_view"}]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 429
    assert response.headers["retry-after"] == "17"
    assert not fake_storage.calls


def test_event_stream_publisher_xadds_one_capped_entry_per_event_when_enabled():
    client = FakeStreamRedis()
    publisher = EventStreamPublisher(client, "cdp:events:raw", 1000, enabled=True)
    received_at = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)

    published = publisher.publish(
        SOURCE_ID, [{"event_name": "page_view"}, {"event_name": "click"}], received_at
    )

    assert published == 2
    assert len(client.pipeline_obj.adds) == 2
    key, fields, maxlen, approximate = client.pipeline_obj.adds[0]
    assert key == "cdp:events:raw"
    assert fields["data_source_id"] == str(SOURCE_ID)
    assert maxlen == 1000 and approximate is True
    assert json.loads(fields["payload"])["event"] == {"event_name": "page_view"}


def test_event_stream_publisher_is_noop_when_disabled():
    client = FakeStreamRedis()
    publisher = EventStreamPublisher(client, "cdp:events:raw", 1000, enabled=False)

    assert publisher.publish(SOURCE_ID, [{"event_name": "x"}], datetime.now(timezone.utc)) == 0
    assert client.pipeline_obj.adds == []


def test_event_stream_publisher_is_best_effort_on_redis_error():
    client = FakeStreamRedis(error=RedisError("stream down"))
    publisher = EventStreamPublisher(client, "cdp:events:raw", 1000, enabled=True)

    # A broker outage must never raise into the ingestion path.
    assert publisher.publish(SOURCE_ID, [{"event_name": "x"}], datetime.now(timezone.utc)) == 0


def test_ingest_publishes_enriched_events_to_the_event_stream():
    stream = CapturingStream()
    service = TrackingLogService(FakeStorage(), FakeSessionCache(), stream)

    service.ingest(SOURCE_ID, [{"event_name": "page_view"}], session_id="s-1", user_id="u-1")

    assert stream.calls and stream.calls[0][0] == SOURCE_ID
    assert stream.calls[0][1][0]["session_id"] == "s-1"
    assert stream.calls[0][1][0]["user_id"] == "u-1"
