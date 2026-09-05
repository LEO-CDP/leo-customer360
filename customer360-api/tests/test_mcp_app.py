import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from core.apps import mcp_app as mcp_module
from core.mcptools import search_data_sources_tool as search_tool_module


class _FakeFastMCP:
    last_instance = None

    def __init__(self, name: str):
        self.name = name
        self.tools: dict[str, object] = {}
        self.attached_to = None
        _FakeFastMCP.last_instance = self

    def tool(self):
        def _decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return _decorator

    def attach(self, target_app):
        self.attached_to = target_app


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False
        self.executed_stmt = None

    def execute(self, stmt):
        self.executed_stmt = stmt
        return _FakeResult(self._rows)

    def close(self):
        self.closed = True


def test_create_mcp_app_exposes_health_endpoint(monkeypatch):
    monkeypatch.setattr(mcp_module, "FastMCP", _FakeFastMCP)

    app = mcp_module.create_mcp_app()
    app.dependency_overrides[mcp_module._bind_tenant_context] = lambda: None

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"service": "customer360-mcp", "status": "ok"}


def test_search_data_sources_by_name_returns_only_expected_fields(monkeypatch):
    monkeypatch.setattr(mcp_module, "FastMCP", _FakeFastMCP)

    row = SimpleNamespace(
        data_source_id=uuid.uuid4(),
        name="Google Analytics Main",
        total_tracked_event=12345,
        avg_daily_event=456,
        avg_events_per_profile=2.5,
        javascript_tags=["<script>a</script>"],
        qr_code_data={"utm_medium": "qr_code"},
    )
    state: dict[str, _FakeSession] = {}

    def _fake_session_local():
        state["session"] = _FakeSession([row])
        return state["session"]

    monkeypatch.setattr(search_tool_module, "SessionLocal", _fake_session_local)
    monkeypatch.setattr(search_tool_module, "get_bound_tenant_id", lambda: str(uuid.uuid4()))

    mcp_module.create_mcp_app()
    tool = _FakeFastMCP.last_instance.tools["search_data_sources_by_name"]

    result = tool("google analytics", limit=5)

    assert result["count"] == 1
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert set(item.keys()) == {
        "id",
        "name",
        "total_tracked_event",
        "avg_daily_event",
        "avg_events_per_profile",
        "javascript_tags",
        "qr_code_data",
    }
    assert item["name"] == "Google Analytics Main"
    assert state["session"].closed is True


def test_search_data_sources_by_name_empty_keywords_short_circuit(monkeypatch):
    monkeypatch.setattr(mcp_module, "FastMCP", _FakeFastMCP)

    def _should_not_open_session():
        raise AssertionError("SessionLocal must not be called when keywords are empty")

    monkeypatch.setattr(search_tool_module, "SessionLocal", _should_not_open_session)
    monkeypatch.setattr(search_tool_module, "get_bound_tenant_id", lambda: str(uuid.uuid4()))

    mcp_module.create_mcp_app()
    tool = _FakeFastMCP.last_instance.tools["search_data_sources_by_name"]

    result = tool("   ", limit=5)

    assert result == {"count": 0, "items": []}


def test_search_data_sources_requires_bound_tenant_context(monkeypatch):
    monkeypatch.setattr(mcp_module, "FastMCP", _FakeFastMCP)

    mcp_module.create_mcp_app()
    tool = _FakeFastMCP.last_instance.tools["search_data_sources_by_name"]

    with pytest.raises(HTTPException) as exc_info:
        tool("google", limit=5)
    assert exc_info.value.status_code == 401


def test_tokenize_keywords_deduplicates_and_limits_to_eight_tokens():
    tokens = search_tool_module._tokenize_keywords(
        "GA ga  web web  mobile crm ads push email social extra"
    )

    assert tokens == ["ga", "web", "mobile", "crm", "ads", "push", "email", "social"]
