"""Tests for API-only simulated traffic seeding."""

from datetime import datetime, timezone
from uuid import UUID

import seed_api_data


def test_generator_creates_canonical_api_events_without_storage_clients():
    config = seed_api_data.ApiSeedConfig(
        event_count=3,
        events_per_session=2,
        profile_count=2,
        seed=7,
    )
    generator = seed_api_data.ApiTrafficGenerator(
        config,
        clock=datetime(2026, 9, 18, 12, tzinfo=timezone.utc),
    )

    sessions = list(generator.iter_sessions())
    events = [event for session in sessions for event in session["events"]]

    assert len(sessions) == 2
    assert len(events) == 3
    assert all(UUID(event["event_id"]) for event in events)
    assert all(event["source_system"] == "api-seed-simulator" for event in events)
    assert all(event["event_dedup_key"].startswith("api-seed:") for event in events)
    assert all(event["properties"]["traffic_type"] == "synthetic_internet" for event in events)


def test_run_api_seed_uses_only_tracking_http_client(monkeypatch):
    sent = []

    class FakeTrackingClient:
        def __init__(self, endpoint, timeout):
            sent.append(("client", endpoint, timeout))

        def send(self, **kwargs):
            sent.append(("send", kwargs))
            return {"accepted": True}

    monkeypatch.setattr(seed_api_data, "TrackingLogClient", FakeTrackingClient)
    config = seed_api_data.ApiSeedConfig(
        event_count=4,
        events_per_session=2,
        concurrency=2,
        queue_drain_timeout_seconds=0,
        seed=9,
    )

    assert seed_api_data.run_api_seed(config) == 0
    assert sent[0][0] == "client"
    assert len([item for item in sent if item[0] == "send"]) == 2
    assert sum(len(item[1]["events"]) for item in sent if item[0] == "send") == 4


def test_config_rejects_empty_or_non_http_settings():
    try:
        seed_api_data.ApiSeedConfig(tracking_api_url="localhost:8010", event_count=1)
    except ValueError as exc:
        assert "http" in str(exc)
    else:
        raise AssertionError("Expected invalid URL to be rejected")


def test_config_does_not_inherit_web_simulator_endpoint(monkeypatch):
    monkeypatch.setattr(seed_api_data, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.delenv("SEED_TRACKING_API_URL", raising=False)
    monkeypatch.setenv("TRACKING_API_URL", "https://public.example.test/tracking/logs")

    config = seed_api_data.ApiSeedConfig.from_environment()

    assert config.tracking_api_url == seed_api_data.DEFAULT_TRACKING_API_URL


def test_queue_status_url_matches_tracking_route():
    assert seed_api_data._queue_status_url(
        "http://localhost:8010/api/v1/tracking/logs"
    ) == "http://localhost:8010/api/v1/tracking/queue-status"


def test_queue_wait_returns_when_api_reports_empty_queue(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"queue_depth":0}'

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(seed_api_data.urllib.request, "urlopen", fake_urlopen)
    config = seed_api_data.ApiSeedConfig(queue_drain_timeout_seconds=5)

    assert seed_api_data.wait_for_tracking_queue(config)
    assert captured["url"].endswith("/api/v1/tracking/queue-status")