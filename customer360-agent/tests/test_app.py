"""Smoke tests for the agent HTTP layer: happy-path serialization and the
AIProviderError -> 502 mapping. The planner itself is patched (no LLM calls);
its own logic is covered by test_ai_providers / test_zalo_planner."""

from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from ai_providers.base import AIProviderError
from campaign_planner import GeneratedCampaignPlan

client = TestClient(app_module.app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_plan_campaign_success():
    plan = GeneratedCampaignPlan(
        name="Q4 Win-Back",
        objective="reactivate",
        strategy_summary="two-touch email",
        action_plan=["email 1", "email 2"],
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 31),
        content_item_ids=["c1"],
    )
    with patch.object(app_module, "generate_campaign_plan", return_value=plan):
        resp = client.post("/plan/email", json={"segment_context": {}, "objective": "reactivate"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Q4 Win-Back"
    assert body["start_date"] == "2026-10-01"
    assert body["content_item_ids"] == ["c1"]


def test_plan_campaign_provider_error_maps_to_502():
    with patch.object(app_module, "generate_campaign_plan", side_effect=AIProviderError("boom")):
        resp = client.post("/plan/email", json={"segment_context": {}, "objective": "x"})
    assert resp.status_code == 502
    assert "boom" in resp.json()["detail"]


def _plan(headers=None):
    plan = GeneratedCampaignPlan(name="n", objective="o", strategy_summary="s")
    with patch.object(app_module, "generate_campaign_plan", return_value=plan):
        return client.post(
            "/plan/email", json={"segment_context": {}, "objective": "x"}, headers=headers or {}
        )


def test_plan_requires_token_when_configured():
    with patch.object(app_module.settings, "api_token", "s3cret"):
        assert _plan().status_code == 401                                   # missing
        assert _plan({"Authorization": "Bearer wrong"}).status_code == 401  # wrong
        assert _plan({"Authorization": "Bearer s3cret"}).status_code == 200 # correct


def test_health_is_open_even_when_token_configured():
    with patch.object(app_module.settings, "api_token", "s3cret"):
        assert client.get("/health").status_code == 200


def test_auth_disabled_when_token_blank():
    with patch.object(app_module.settings, "api_token", ""):
        assert _plan().status_code == 200
