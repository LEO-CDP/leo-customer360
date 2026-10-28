"""Unit tests for email campaign planner candidate-list guardrails."""

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
    monkeypatch.setattr(cp.base, "_resolve_provider", lambda *args, **kwargs: _FakeProvider(response))
    monkeypatch.setattr(cp.base, "_instructions", lambda _key: "Email instructions")


_CANDIDATES = [
    {"content_item_id": "content-1", "title": "Welcome"},
    {"content_item_id": "content-2", "title": "Offer"},
]

_GOOD = {
    "name": "Reactivation",
    "objective": "reactivate",
    "strategy_summary": "win-back",
    "action_plan": ["send email"],
    "start_date": "2999-01-01",
    "end_date": "2999-01-07",
    "content_item_ids": ["content-1"],
}


def _brief():
    return cp.CampaignPlanBrief(segment_context={"size": 100}, objective="reactivate")


def test_happy_path_returns_candidate_content_ids(monkeypatch):
    _patch_provider(monkeypatch, _GOOD)

    plan = cp.generate_campaign_plan(_brief(), _CANDIDATES)

    assert plan.content_item_ids == ["content-1"]


def test_off_list_content_id_is_rejected(monkeypatch):
    _patch_provider(monkeypatch, dict(_GOOD, content_item_ids=["invented-content"]))

    with pytest.raises(AIProviderError, match="not in the candidate list"):
        cp.generate_campaign_plan(_brief(), _CANDIDATES)


def test_duplicate_content_id_is_rejected(monkeypatch):
    _patch_provider(monkeypatch, dict(_GOOD, content_item_ids=["content-1", "content-1"]))

    with pytest.raises(AIProviderError, match="duplicate content_item_ids"):
        cp.generate_campaign_plan(_brief(), _CANDIDATES)