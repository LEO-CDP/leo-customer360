"""Functional wiring: every agent runs through the service's real LLMProvider,
with only litellm.completion replaced. This is what the agent unit tests, which
use a fake `.complete()`, cannot show: that the production adapter satisfies
each agent and that provider failures end up as the intended outcome."""

import json
import types

import litellm
import pytest

from ai_providers.base import AIProviderError
from ai_providers.provider import LLMProvider, ProviderState
from use_cases.analytics_chat import AnalyticsChat
from use_cases.campaign_orchestrator import Tool, run_orchestration
from use_cases.copy_drafter import draft_copy
from use_cases.performance_narrative import narrate
from use_cases.persona_decision_loop import ALLOWED_ACTIONS, decide
from use_cases.schema_mapper import propose_mappings
from use_cases.support_answerer import answer_support_question
from use_cases.text_to_sql import answer_question
from use_cases.ticket_classifier import classify_ticket


def _wire(monkeypatch, reply):
    """A real LLMProvider whose litellm.completion returns `reply`."""
    content = reply if isinstance(reply, str) else json.dumps(reply)

    def completion(**kwargs):
        completion.calls.append(kwargs)
        message = types.SimpleNamespace(content=content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    completion.calls = []
    monkeypatch.setattr(litellm, "completion", completion)
    return LLMProvider(ProviderState(model="test/model", api_key="test-key")), completion


def _failing(monkeypatch, message="upstream timeout"):
    def completion(**kwargs):
        raise RuntimeError(message)

    monkeypatch.setattr(litellm, "completion", completion)
    return LLMProvider(ProviderState(model="test/model", api_key="test-key"))


# ---- each agent over the real adapter ----


def test_ticket_classifier(monkeypatch):
    provider, completion = _wire(monkeypatch, {"urgency": "high", "sentiment": "negative", "team": "billing"})

    result = classify_ticket("FIXTURE: I was charged twice.", provider)

    assert (result.urgency, result.team, result.needs_review) == ("high", "billing", False)
    assert completion.calls[0]["model"] == "test/model"
    assert "charged twice" in completion.calls[0]["messages"][0]["content"]


def test_schema_mapper(monkeypatch):
    provider, _ = _wire(monkeypatch, {"mappings": [{"source": "cust_email", "target": "primary_email", "confidence": 0.95}]})

    result = propose_mappings([{"name": "cust_email"}], {"primary_email": "primary_email: main email"}, provider)

    assert (result[0].status, result[0].target) == ("mapped", "primary_email")


def test_copy_drafter(monkeypatch):
    provider, _ = _wire(monkeypatch, {
        "subject": "We saved your spot", "preview_text": "A quick note",
        "email_body": "Hi {{first_name}}, your plan is here.\n{{unsubscribe_url}}", "social_caption": "Your plan is waiting.",
    })

    draft = draft_copy({"objective": "win back", "audience": "lapsed"}, ["Plain, warm voice."], provider)

    assert draft.subject == "We saved your spot"
    assert draft.blocked is False


def test_performance_narrative(monkeypatch):
    provider, _ = _wire(monkeypatch, "Beta sent 2,500 emails.")

    result = narrate("How did campaigns do?", [{"campaign": "Beta", "sent": 2500}], provider)

    assert result.text == "Beta sent 2,500 emails."
    assert result.ungrounded == []


def test_text_to_sql(monkeypatch):
    provider, _ = _wire(monkeypatch, "SELECT COUNT(*) AS n FROM cdp_segments")

    result = answer_question("how many segments", provider, {"cdp_segments": "cdp_segments: segment_id"}, lambda sql: [{"n": 8}])

    assert result.rows == [{"n": 8}]


def test_analytics_chat(monkeypatch):
    sql_provider, _ = _wire(monkeypatch, "SELECT COUNT(*) AS n FROM cdp_segments")
    narrative_provider = LLMProvider(ProviderState(model="test/narrative", api_key="k"))

    chat = AnalyticsChat(sql_provider, narrative_provider, {"cdp_segments": "cdp_segments: segment_id"}, lambda sql: [{"n": 8}])
    turn = chat.ask("how many segments")

    assert turn.error is None
    assert turn.chart == {"type": "metric", "y": ["n"]}


def test_support_answerer(monkeypatch):
    provider, _ = _wire(monkeypatch, "Use Settings.")

    result = answer_support_question(
        "How do I reset my password?", provider,
        lambda q: [{"url": "https://help.example.test/p", "title": "Passwords", "text": "Reset it in Settings."}],
    )

    assert (result.found, result.source_kind) == (True, "kb")


def test_persona_decision_loop(monkeypatch):
    evidence = {"persona": {"risk_level": "high"}, "features": {"churn_probability": 0.7}}
    provider, _ = _wire(monkeypatch, {
        "perception": ["Churn probability is 0.7"], "interpretation": "At risk of leaving.",
        "action": {"code": ALLOWED_ACTIONS[-1], "channel": "email", "rationale": "High churn probability."},
        "cited": ["features.churn_probability"],
    })

    assert decide(evidence, provider).needs_review is False


def test_campaign_orchestrator(monkeypatch):
    provider, _ = _wire(monkeypatch, {"action": "finish", "summary": "Nothing to do."})

    result = run_orchestration("goal", {"t": Tool("t", "d", lambda: 1)}, provider)

    assert (result.status, result.summary) == ("finished", "Nothing to do.")


# ---- provider failure and misconfiguration ----


def test_outage_flags_classifier_and_persona_for_review_instead_of_guessing(monkeypatch):
    provider = _failing(monkeypatch)

    ticket = classify_ticket("FIXTURE: help", provider)
    persona = decide({"persona": {"risk_level": "high"}}, provider)

    assert ticket.needs_review and "upstream timeout" in ticket.reason
    assert persona.needs_review and persona.action is None


def test_outage_stops_the_orchestrator_without_running_any_tool(monkeypatch):
    ran = []
    provider = _failing(monkeypatch)

    result = run_orchestration("goal", {"t": Tool("t", "d", lambda: ran.append(1))}, provider, max_steps=3)

    assert result.status == "step_limit"
    assert ran == []


@pytest.mark.parametrize(
    "call",
    [
        lambda p: answer_question("how many segments", p, {"cdp_segments": "cdp_segments: id"}, lambda s: []),
        lambda p: narrate("q", [{"a": 1}], p),
        lambda p: draft_copy({"objective": "x", "audience": "y"}, ["g"], p),
        lambda p: propose_mappings([{"name": "c"}], {"f": "f: d"}, p),
        lambda p: answer_support_question("q", p, lambda q: [{"url": "https://h.example.test/a", "text": "t"}]),
    ],
    ids=["text_to_sql", "narrative", "copy", "schema_mapper", "support"],
)
def test_outage_surfaces_as_a_provider_error_for_agents_that_have_no_safe_default(monkeypatch, call):
    with pytest.raises(AIProviderError):
        call(_failing(monkeypatch))


def test_unconfigured_provider_fails_clearly_before_any_call(monkeypatch):
    called = []
    monkeypatch.setattr(litellm, "completion", lambda **kw: called.append(kw))
    provider = LLMProvider(ProviderState(model="test/model"))

    with pytest.raises(AIProviderError, match="LLM not configured"):
        narrate("q", [{"a": 1}], provider)

    assert called == []
