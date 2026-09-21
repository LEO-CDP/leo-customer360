"""End-to-end tests for the AI Agent HTTP API — the FULL request stack, with only
the external LLM mocked (litellm.completion) and the prompt store fed an in-memory
snapshot (what database-init seeds in prod). Everything else is real: routing,
bearer-token auth, request->brief mapping, prompt assembly from the store, the
provider, JSON parsing, the Zalo template guardrails, and response serialization.

Answers the operational question "can the agent actually be used?" for both
channels (email + Zalo ZNS): the happy path, the auth gate, and every way an LLM
answer is rejected (bad JSON, off-list template, missing param, no LLM key).
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# The E2E path runs the real provider, which calls litellm.completion — mock it.
pytest.importorskip("litellm")

from fastapi.testclient import TestClient

import app as app_module
import config
from campaign_planner.base import CAMPAIGN_PLAN_INSTRUCTIONS, CAMPAIGN_ZNS_INSTRUCTIONS
from prompts import PromptTemplate, get_store, reset_store_cache

client = TestClient(app_module.app)

# Bodies mimic what database-init seeds. Their content is irrelevant to the mocked
# LLM, but their presence in the built prompt proves the real assembly path ran.
_EMAIL_BODY = "You are a campaign planner. Return a JSON object for the email campaign."
_ZNS_BODY = "You are a ZNS planner. Pick one approved template_id and fill its params. Return JSON."


def _fake_completion(text: str):
    """The OpenAI-shaped object LiteLLM returns (choices[0].message.content)."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


@pytest.fixture(autouse=True)
def agent_env(monkeypatch):
    """Seed the prompt-store snapshot (no DB) and configure a hosted LLM key so the
    provider is 'configured'. Auth is off by default; the auth test opts in."""
    reset_store_cache()
    get_store()._snapshot = {
        CAMPAIGN_PLAN_INSTRUCTIONS: PromptTemplate(key=CAMPAIGN_PLAN_INSTRUCTIONS, body=_EMAIL_BODY),
        CAMPAIGN_ZNS_INSTRUCTIONS: PromptTemplate(key=CAMPAIGN_ZNS_INSTRUCTIONS, body=_ZNS_BODY),
    }
    monkeypatch.setattr(config.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(config.settings, "llm_base_url", "")
    monkeypatch.setattr(config.settings, "api_token", "")
    yield
    reset_store_cache()


def test_health_end_to_end():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# --- Email channel -----------------------------------------------------------

_EMAIL_REQUEST = {
    "segment_context": {"segment": "dormant-90d", "size": 1200},
    "objective": "reactivate lapsed customers",
    "candidate_content_items": [
        {"content_item_id": "c1", "title": "We miss you"},
        {"content_item_id": "c2", "title": "20% back"},
    ],
}

_EMAIL_LLM_JSON = {
    "name": "Q4 Win-Back",
    "objective": "reactivate lapsed customers",
    "strategy_summary": "two-touch reactivation email",
    "action_plan": ["email 1: we miss you", "email 2: incentive"],
    "start_date": "2999-10-01",
    "end_date": "2999-10-31",
    "content_item_ids": ["c1", "c2"],
}


def test_email_plan_end_to_end():
    # Wrap in a ```json fence to also exercise fence-stripping on the real path.
    fenced = "```json\n" + json.dumps(_EMAIL_LLM_JSON) + "\n```"
    with patch("litellm.completion", return_value=_fake_completion(fenced)) as mock_llm:
        resp = client.post("/plan/email", json=_EMAIL_REQUEST)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Q4 Win-Back"
    assert body["start_date"] == "2999-10-01"
    assert body["content_item_ids"] == ["c1", "c2"]
    # The prompt the agent actually sent must carry the seeded instruction body AND
    # the candidate items — proof the full assembly path ran (not a stub).
    prompt = mock_llm.call_args.kwargs["messages"][0]["content"]
    assert _EMAIL_BODY in prompt
    assert "c1" in prompt


def test_email_bad_llm_json_maps_to_502():
    with patch("litellm.completion", return_value=_fake_completion("sorry, I can't")):
        resp = client.post("/plan/email", json=_EMAIL_REQUEST)
    assert resp.status_code == 502  # unusable LLM output -> clean 502, not a 500


def test_llm_not_configured_maps_to_502(monkeypatch):
    # Mirrors the current UAT box (no LLM key yet): the agent is up, but /plan 502s
    # BEFORE calling the SDK. This is the "is it usable?" gate in the negative.
    monkeypatch.setattr(config.settings, "llm_api_key", "")
    monkeypatch.setattr(config.settings, "llm_base_url", "")
    with patch("litellm.completion") as mock_llm:
        resp = client.post("/plan/email", json=_EMAIL_REQUEST)
    assert resp.status_code == 502
    mock_llm.assert_not_called()


# --- Zalo ZNS channel --------------------------------------------------------

_ZNS_TEMPLATES = [
    {"template_id": "tpl-otp", "name": "OTP", "params": ["otp", "name"]},
    {"template_id": "tpl-promo", "name": "Promo", "params": ["offer"]},
]

_ZNS_REQUEST = {
    "segment_context": {"segment": "cart-abandoners", "size": 300},
    "objective": "recover abandoned carts",
    "candidate_templates": _ZNS_TEMPLATES,
}

_ZNS_LLM_JSON = {
    "template_id": "tpl-promo",
    "template_data": {"offer": "15% off, today only"},
    "name": "Cart Recovery",
    "objective": "recover abandoned carts",
    "strategy_summary": "single ZNS nudge",
    "action_plan": ["send promo ZNS"],
    "start_date": "2999-01-01",
    "end_date": "2999-01-07",
}


def test_zalo_plan_end_to_end():
    with patch("litellm.completion", return_value=_fake_completion(json.dumps(_ZNS_LLM_JSON))) as mock_llm:
        resp = client.post("/plan/zalo", json=_ZNS_REQUEST)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["template_id"] == "tpl-promo"
    assert body["template_data"] == {"offer": "15% off, today only"}
    assert body["name"] == "Cart Recovery"
    prompt = mock_llm.call_args.kwargs["messages"][0]["content"]
    assert _ZNS_BODY in prompt
    assert "tpl-promo" in prompt


def test_zalo_off_list_template_maps_to_502():
    # Guardrail: the AI must pick a template_id from the candidate list.
    bad = dict(_ZNS_LLM_JSON, template_id="tpl-not-approved")
    with patch("litellm.completion", return_value=_fake_completion(json.dumps(bad))):
        resp = client.post("/plan/zalo", json=_ZNS_REQUEST)
    assert resp.status_code == 502


def test_zalo_missing_required_param_maps_to_502():
    # Guardrail: 'name' is required by tpl-otp but omitted -> ZNS send would fail.
    bad = dict(_ZNS_LLM_JSON, template_id="tpl-otp", template_data={"otp": "123456"})
    with patch("litellm.completion", return_value=_fake_completion(json.dumps(bad))):
        resp = client.post("/plan/zalo", json=_ZNS_REQUEST)
    assert resp.status_code == 502


# --- Auth: the shared bearer token deployed to the box -----------------------

def test_plan_requires_bearer_token_end_to_end(monkeypatch):
    monkeypatch.setattr(config.settings, "api_token", "s3cret")
    with patch("litellm.completion", return_value=_fake_completion(json.dumps(_EMAIL_LLM_JSON))):
        assert client.post("/plan/email", json=_EMAIL_REQUEST).status_code == 401
        assert client.post(
            "/plan/email", json=_EMAIL_REQUEST, headers={"Authorization": "Bearer wrong"}
        ).status_code == 401
        assert client.post(
            "/plan/email", json=_EMAIL_REQUEST, headers={"Authorization": "Bearer s3cret"}
        ).status_code == 200
