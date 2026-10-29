"""Tests for API-only simulated traffic seeding."""

from datetime import datetime, timezone
import sys
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


def test_generator_creates_uuid_event_ids_without_seed():
    config = seed_api_data.ApiSeedConfig(event_count=2, events_per_session=2)
    generator = seed_api_data.ApiTrafficGenerator(
        config,
        clock=datetime(2026, 9, 18, 12, tzinfo=timezone.utc),
    )

    events = [event for session in generator.iter_sessions() for event in session["events"]]

    assert len(events) == 2
    assert all(UUID(event["event_id"]) for event in events)


def test_default_seed_covers_over_5000_identities_in_six_domains():
    config = seed_api_data.ApiSeedConfig(seed=7)
    identities = {}
    event_count = 0
    for session in seed_api_data.ApiTrafficGenerator(config).iter_sessions():
        event_count += len(session["events"])
        domains = {event["domain"] for event in session["events"]}
        assert len(domains) == 1
        identities[session["user_id"]] = domains.pop()

    assert event_count == 24_000
    assert len(identities) == 6_000
    assert set(identities.values()) == {
        "retail", "education", "real_estate", "travel", "banking", "media",
    }


def test_repeated_sessions_keep_domain_and_unseeded_runs_use_fresh_identities():
    config = seed_api_data.ApiSeedConfig(
        event_count=16, events_per_session=2, profile_count=6,
    )
    first = list(seed_api_data.ApiTrafficGenerator(config).iter_sessions())
    second = list(seed_api_data.ApiTrafficGenerator(config).iter_sessions())
    assert {s["user_id"] for s in first}.isdisjoint({s["user_id"] for s in second})
    for session in first:
        previous = [s for s in first if s["user_id"] == session["user_id"]]
        assert len({event["domain"] for s in previous for event in s["events"]}) == 1


def test_default_seed_submits_all_identities_through_tracking_client(monkeypatch):
    sent = []

    class FakeTrackingClient:
        def __init__(self, endpoint, timeout):
            pass

        def send(self, **kwargs):
            sent.append(kwargs)
            return {"accepted": True, "event_count": len(kwargs["events"])}

    monkeypatch.setattr(seed_api_data, "TrackingLogClient", FakeTrackingClient)
    config = seed_api_data.ApiSeedConfig(seed=7, queue_drain_timeout_seconds=0)

    assert seed_api_data.run_api_seed(config) == 0
    assert len({event["user_id"] for request in sent for event in request["events"]}) == 6_000
    assert 24 <= len(sent) <= 27
    assert all(len(request["events"]) <= 1_000 for request in sent)
    assert all(request["user_id"] is None and request["session_id"] is None for request in sent)
    assert all(len({event["device_type"] for event in request["events"]}) == 1 for request in sent)
    assert sum(len(request["events"]) for request in sent) == 24_000
    assert {event["domain"] for request in sent for event in request["events"]} == set(
        seed_api_data.DOMAINS
    )


def test_launcher_minimum_overrides_small_environment_counts(monkeypatch):
    monkeypatch.setattr(seed_api_data, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("NEW_DATA_EVENT_COUNT", "20000")
    monkeypatch.setenv("SEED_EVENTS_PER_SESSION", "20")
    monkeypatch.setenv("SEED_PROFILE_COUNT", "1000")
    monkeypatch.setattr(sys, "argv", ["seed_api_data.py", "--min-profiles", "6000"])
    configs = []
    monkeypatch.setattr(
        seed_api_data, "run_api_seed",
        lambda config, *, dry_run: configs.append(config) or 0,
    )
    assert seed_api_data.main() == 0
    assert configs[0].profile_count == 6_000
    assert configs[0].event_count == 120_000


def test_run_api_seed_uses_only_tracking_http_client(monkeypatch):
    sent = []

    class FakeTrackingClient:
        def __init__(self, endpoint, timeout):
            sent.append(("client", endpoint, timeout))

        def send(self, **kwargs):
            sent.append(("send", kwargs))
            return {"accepted": True, "event_count": len(kwargs["events"])}

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
    assert 1 <= len([item for item in sent if item[0] == "send"]) <= 2
    assert sum(len(item[1]["events"]) for item in sent if item[0] == "send") == 4


def test_seed_reports_failure_when_tracking_queue_does_not_drain(monkeypatch):
    class FakeTrackingClient:
        def __init__(self, endpoint, timeout):
            pass

        def send(self, **kwargs):
            return {"accepted": True, "event_count": len(kwargs["events"])}

    monkeypatch.setattr(seed_api_data, "TrackingLogClient", FakeTrackingClient)
    monkeypatch.setattr(seed_api_data, "wait_for_tracking_queue", lambda config: False)
    config = seed_api_data.ApiSeedConfig(event_count=1, events_per_session=1)
    assert seed_api_data.run_api_seed(config) == 1


def test_seed_reports_filtered_tracking_requests(monkeypatch):
    class FakeTrackingClient:
        def __init__(self, endpoint, timeout):
            pass

        def send(self, **kwargs):
            return {"accepted": False, "filtered": True, "filter_reason": "bot_user_agent"}

    monkeypatch.setattr(seed_api_data, "TrackingLogClient", FakeTrackingClient)
    config = seed_api_data.ApiSeedConfig(event_count=1, events_per_session=1)
    assert seed_api_data.run_api_seed(config) == 1


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