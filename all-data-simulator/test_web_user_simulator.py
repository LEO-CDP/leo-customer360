import json
import random
from uuid import UUID

from web_user_simulator import (
    AnalyticsApiClient,
    AnalyticsApiError,
    AgentConfig,
    AgentJourneyError,
    DEFAULT_DATA_SOURCE_ID,
    MinioTrackingVerifier,
    TrackingLogClient,
    TOOL_DEFINITIONS,
    UserProfile,
    WebUserAgent,
)


def test_analytics_client_triggers_polls_and_verifies_summary(monkeypatch):
    responses = iter(
        [
            {"status": "submitted", "run_id": "run-1"},
            {"run_id": "run-1", "status": "running"},
            {"run_id": "run-1", "status": "success"},
            {
                "data_source_id": DEFAULT_DATA_SOURCE_ID,
                "total_tracked_event": 4,
                "avg_daily_event": 4,
                "avg_events_per_profile": 4.0,
            },
        ]
    )
    requests = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(next(responses)).encode()

    def fake_urlopen(request, timeout):
        requests.append((request.get_method(), request.full_url, timeout))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("web_user_simulator.time.sleep", lambda _seconds: None)
    client = AnalyticsApiClient("http://customer360.test/api/v1", timeout_seconds=3.0)

    run_id = client.trigger_analytics_hourly_schedule()
    status = client.wait_for_analytics_schedule(run_id, poll_interval=1.0, timeout=10.0)
    summary = client.verify_data_source_summary(UUID(DEFAULT_DATA_SOURCE_ID))

    assert run_id == "run-1"
    assert status["status"] == "success"
    assert summary["total_tracked_event"] == 4
    assert [request[1] for request in requests] == [
        "http://customer360.test/api/v1/analytics/source-analytics/process",
        "http://customer360.test/api/v1/analytics/source-analytics/status/run-1",
        "http://customer360.test/api/v1/analytics/source-analytics/status/run-1",
        f"http://customer360.test/api/v1/metadata/data-sources/{DEFAULT_DATA_SOURCE_ID}",
    ]


def test_analytics_client_logs_in_once_before_protected_requests(monkeypatch):
    responses = iter(
        [
            {"access_token": "test-token"},
            {"status": "submitted", "run_id": "run-1"},
        ]
    )
    requests = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(next(responses)).encode()

    def fake_urlopen(request, timeout):
        requests.append((request.get_method(), request.full_url, request.headers, request.data))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = AnalyticsApiClient(
        "https://c360.example.com/c360api/api/v1/",
        token=None,
        username="admin",
        password="test-password",
    )

    assert client.trigger_analytics_hourly_schedule() == "run-1"
    assert requests[0][0:2] == (
        "POST",
        "https://c360.example.com/c360api/api/v1/auth/login",
    )
    assert json.loads(requests[0][3]) == {
        "username": "admin",
        "password": "test-password",
    }
    assert requests[1][0:2] == (
        "POST",
        "https://c360.example.com/c360api/api/v1/analytics/source-analytics/process",
    )
    assert requests[1][2]["Authorization"] == "Bearer test-token"


def test_analytics_client_rejects_non_positive_summary(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"total_tracked_event": 0, "avg_daily_event": 0, "avg_events_per_profile": 0}'

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse())
    client = AnalyticsApiClient("http://customer360.test/api/v1")

    try:
        client.verify_data_source_summary(UUID(DEFAULT_DATA_SOURCE_ID))
    except AnalyticsApiError as exc:
        assert "metrics were not updated" in str(exc)
    else:
        raise AssertionError("Expected AnalyticsApiError")


def test_offline_agent_emits_complete_purchase_journey():
    agent = WebUserAgent(
        UserProfile(
            user_id="web-user-test",
            session_id="web-session-test",
            name="Test User",
            budget_vnd=2_000_000,
            preferred_category="audio",
        ),
        AgentConfig(offline=True),
        rng=random.Random(7),
    )

    events = agent.run()

    assert [event["event_name"] for event in events] == [
        "ad_impression",
        "product_view",
        "price_request",
        "purchase",
    ]
    assert events[-1]["product_id"] == events[0]["product_id"]
    assert events[-1]["agent_mode"] == "offline"


def test_luna_tool_requests_disable_reasoning_effort():
    captured = {}

    class FakeToolCall:
        id = "call-1"
        function = type(
            "Function",
            (),
            {"name": "see_ad", "arguments": '{"ad_id":"ad-001"}'},
        )()

        def model_dump(self):
            return {
                "id": self.id,
                "type": "function",
                "function": {
                    "name": self.function.name,
                    "arguments": self.function.arguments,
                },
            }

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            message = type(
                "Message",
                (),
                {"content": None, "tool_calls": [FakeToolCall()]},
            )()
            return type("Response", (), {"choices": [type("Choice", (), {"message": message})()]})()

    client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": FakeCompletions()})()},
    )()
    agent = WebUserAgent(
        UserProfile("user", "session", "Test User", 2_000_000, "audio"),
        AgentConfig(openai_api_key="test-key", max_steps=1),
        openai_client=client,
    )

    try:
        agent.run()
    except AgentJourneyError:
        pass

    assert captured["reasoning_effort"] == "none"
    assert captured["tools"] == TOOL_DEFINITIONS


def test_model_agent_completes_valid_purchase_sequence():
    class FakeToolCall:
        def __init__(self, call_id, name, arguments):
            self.id = call_id
            self.function = type(
                "Function",
                (),
                {"name": name, "arguments": json.dumps(arguments)},
            )()

        def model_dump(self):
            return {
                "id": self.id,
                "type": "function",
                "function": {
                    "name": self.function.name,
                    "arguments": self.function.arguments,
                },
            }

    class FakeCompletions:
        def __init__(self):
            self.actions = iter(
                [
                    ("see_ad", {"ad_id": "ad-001"}),
                    ("view_product", {"product_id": "product-001"}),
                    ("ask_price", {"product_id": "product-001"}),
                    ("purchase_product", {"product_id": "product-001", "quantity": 1}),
                ]
            )

        def create(self, **_kwargs):
            name, arguments = next(self.actions)
            message = type(
                "Message",
                (),
                {
                    "content": None,
                    "tool_calls": [FakeToolCall("call-1", name, arguments)],
                },
            )()
            return type("Response", (), {"choices": [type("Choice", (), {"message": message})()]})()

    completions = FakeCompletions()
    client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": completions})()},
    )()
    agent = WebUserAgent(
        UserProfile("user", "session", "Test User", 2_000_000, "audio"),
        AgentConfig(openai_api_key="test-key", max_steps=4),
        openai_client=client,
    )

    events = agent.run()

    assert [event["event_name"] for event in events] == [
        "ad_impression",
        "product_view",
        "price_request",
        "purchase",
    ]


def test_analytics_client_rejects_summary_for_wrong_data_source(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "data_source_id": "22222222-2222-2222-2222-222222222222",
                    "total_tracked_event": 4,
                    "avg_daily_event": 4,
                    "avg_events_per_profile": 4.0,
                }
            ).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse())
    client = AnalyticsApiClient("http://customer360.test/api/v1")

    try:
        client.verify_data_source_summary(UUID(DEFAULT_DATA_SOURCE_ID))
    except AnalyticsApiError as exc:
        assert "belongs to data source" in str(exc)
    else:
        raise AssertionError("Expected AnalyticsApiError")


def test_agent_config_rejects_non_terminating_poll_settings():
    try:
        AgentConfig(analytics_timeout_seconds=0)
    except ValueError as exc:
        assert "analytics_timeout_seconds" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_tracking_client_posts_api_contract(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"event_count": 1}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = TrackingLogClient("http://tracking.test/logs", 4.5).send(
        data_source_id=UUID(DEFAULT_DATA_SOURCE_ID),
        session_id="web-session-test",
        user_id="web-user-test",
        events=[{"event_name": "product_view"}],
    )

    assert result == {"event_count": 1}
    assert captured["url"] == "http://tracking.test/logs"
    assert captured["timeout"] == 4.5
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["payload"] == {
        "data_source_id": DEFAULT_DATA_SOURCE_ID,
        "session_id": "web-session-test",
        "user_id": "web-user-test",
        "events": [{"event_name": "product_view"}],
    }


def test_minio_verifier_matches_stored_ndjson(monkeypatch):
    expected_event = {"event_name": "purchase", "product_id": "product-001"}
    stored_event = {
        **expected_event,
        "session_id": "web-session-test",
        "user_id": "web-user-test",
    }
    verifier = MinioTrackingVerifier.__new__(MinioTrackingVerifier)
    monkeypatch.setattr(
        verifier,
        "read_records",
        lambda _bucket, _object_key: [
            {
                "data_source_id": DEFAULT_DATA_SOURCE_ID,
                "received_at": "2026-09-08T00:00:00+00:00",
                "event": stored_event,
            }
        ],
    )

    records = verifier.verify_batch(
        bucket=f"data-tracking-{DEFAULT_DATA_SOURCE_ID}",
        object_key="2026-09-08-00/batch.jsonl",
        data_source_id=UUID(DEFAULT_DATA_SOURCE_ID),
        session_id="web-session-test",
        user_id="web-user-test",
        expected_events=[expected_event],
    )

    assert records[0]["event"] == stored_event