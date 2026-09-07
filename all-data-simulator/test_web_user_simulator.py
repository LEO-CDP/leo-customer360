import json
import random
from uuid import UUID

from web_user_simulator import (
    AgentConfig,
    DEFAULT_DATA_SOURCE_ID,
    MinioTrackingVerifier,
    TrackingLogClient,
    TOOL_DEFINITIONS,
    UserProfile,
    WebUserAgent,
)


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

    agent.run()

    assert captured["reasoning_effort"] == "none"
    assert captured["tools"] == TOOL_DEFINITIONS


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