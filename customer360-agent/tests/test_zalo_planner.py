"""Unit tests for the AI Zalo ZNS campaign planner guardrails."""

import json

import pytest

import campaign_planner as cp
from ai_providers.base import AIProviderError


class _FakeProvider:
    def __init__(self, response: dict):
        self._response = response

    def complete(self, prompt: str) -> str:
        return json.dumps(self._response)


def _patch_provider(monkeypatch, response: dict):
    # zalo.generate_* calls base._resolve_provider / base._instructions -> patch there.
    monkeypatch.setattr(cp.base, "_resolve_provider", lambda *a, **k: _FakeProvider(response))
    # Prompt bodies live in the DB; stub the store lookup so tests need no DB.
    monkeypatch.setattr(cp.base, "_instructions", lambda _k: "ZNS instructions")


_CANDIDATES = [
    {"template_id": "11111111-1111-1111-1111-111111111111", "name": "OTP", "params": ["otp", "name"]},
    {"template_id": "22222222-2222-2222-2222-222222222222", "name": "Promo", "params": ["offer"]},
]

_GOOD = {
    "template_id": "11111111-1111-1111-1111-111111111111",
    "template_data": {"otp": "123456", "name": "An"},
    "name": "Reactivation", "objective": "reactivate", "strategy_summary": "win-back",
    "action_plan": ["send OTP"], "start_date": "2999-01-01", "end_date": "2999-01-07",
}


def _brief():
    return cp.ZnsCampaignPlanBrief(segment_context={"size": 100}, objective="reactivate")


def test_happy_path_returns_plan(monkeypatch):
    _patch_provider(monkeypatch, _GOOD)
    plan = cp.generate_zalo_campaign_plan(_brief(), _CANDIDATES)
    assert plan.template_id == "11111111-1111-1111-1111-111111111111"
    assert plan.template_data == {"otp": "123456", "name": "An"}
    assert plan.name == "Reactivation"


def test_off_list_template_is_rejected(monkeypatch):
    bad = dict(_GOOD, template_id="99999999-9999-9999-9999-999999999999")
    _patch_provider(monkeypatch, bad)
    with pytest.raises(AIProviderError, match="not in the candidate list"):
        cp.generate_zalo_campaign_plan(_brief(), _CANDIDATES)


def test_missing_required_param_is_rejected(monkeypatch):
    # 'name' required by the chosen template but omitted from template_data.
    bad = dict(_GOOD, template_data={"otp": "123456"})
    _patch_provider(monkeypatch, bad)
    with pytest.raises(AIProviderError, match="missing required ZNS params"):
        cp.generate_zalo_campaign_plan(_brief(), _CANDIDATES)


def test_blank_required_param_is_rejected(monkeypatch):
    bad = dict(_GOOD, template_data={"otp": "123456", "name": "   "})
    _patch_provider(monkeypatch, bad)
    with pytest.raises(AIProviderError, match="missing required ZNS params"):
        cp.generate_zalo_campaign_plan(_brief(), _CANDIDATES)
