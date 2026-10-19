"""HTTP layer for the nine use-case agents. The model call is replaced by a
scripted provider, so these tests cover the contract: request validation,
response shape, provider-outage mapping, auth, and which model each route asks
for. The agents' own logic is covered by their unit tests."""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
import use_case_routes
from ai_providers.base import AIProviderError

client = TestClient(app_module.app)

# Synthetic fixtures, test-only.
SCHEMA = {"cdp_segments": "cdp_segments: segment_id, segment_name, member_count"}
RETENTION = "Trigger a retention/win-back campaign to reduce churn risk."


class _Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.asked: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)


@pytest.fixture
def scripted(monkeypatch):
    """Install a scripted provider for every route; records which use case asked."""

    def install(*replies):
        provider = _Scripted(*replies)

        def provider_for(use_case):
            provider.asked.append(use_case)
            return provider

        monkeypatch.setattr(use_case_routes, "provider_for", provider_for)
        return provider

    return install


# ---- /analytics/sql (use cases 2 and 5) ----


def test_sql_returns_a_checked_proposal(scripted):
    provider = scripted("SELECT COUNT(*) AS n FROM cdp_segments")

    resp = client.post("/analytics/sql", json={"question": "how many segments are there", "schema": SCHEMA})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "sql": "SELECT COUNT(*) AS n FROM cdp_segments", "error": None}
    assert provider.asked == ["sql"]


def test_sql_says_not_found_without_a_model_call_when_no_table_matches(scripted):
    provider = scripted()

    resp = client.post("/analytics/sql", json={"question": "who are our biggest spenders", "schema": SCHEMA})

    assert resp.json()["status"] == "not_found"
    assert provider.prompts == []


def test_sql_refuses_a_write_statement(scripted):
    scripted("DELETE FROM cdp_segments")

    resp = client.post("/analytics/sql", json={"question": "remove all segments", "schema": SCHEMA})

    assert resp.status_code == 200
    assert resp.json()["status"] == "refused"
    assert "SELECT" in resp.json()["error"]


def test_sql_passes_earlier_turns_to_the_model(scripted):
    provider = scripted("SELECT 1")
    history = [{"question": "how many segments", "sql": "SELECT COUNT(*) FROM cdp_segments"}]

    client.post("/analytics/sql", json={"question": "now split that by name", "schema": SCHEMA, "history": history})

    assert "SELECT COUNT(*) FROM cdp_segments" in provider.prompts[0]


@pytest.mark.parametrize(
    "body",
    [
        {"question": "q", "schema": {}},
        {"question": "", "schema": SCHEMA},
        {"question": "q", "schema": SCHEMA, "unexpected": 1},
        {"question": "q", "schema": SCHEMA, "k": 50},
        {"question": "q", "schema": SCHEMA, "history": [{"question": "a", "sql": "b"}] * 6},
    ],
    ids=["empty_schema", "empty_question", "extra_field", "k_too_large", "too_much_history"],
)
def test_sql_rejects_invalid_requests(scripted, body):
    scripted()

    assert client.post("/analytics/sql", json=body).status_code == 422


def test_sql_provider_outage_is_a_502(scripted):
    scripted(AIProviderError("upstream timeout"))

    resp = client.post("/analytics/sql", json={"question": "how many segments", "schema": SCHEMA})

    assert resp.status_code == 502
    assert "upstream timeout" in resp.json()["detail"]


# ---- /analytics/narrative (use case 7, plus the chat's chart shape) ----


def test_narrative_returns_text_and_a_chart_shape(scripted):
    provider = scripted("Beta sent 2,500 emails.")

    resp = client.post(
        "/analytics/narrative",
        json={"question": "How did campaigns do?", "rows": [{"campaign": "Beta", "sent": 2500}]},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "narrative": "Beta sent 2,500 emails.",
        "ungrounded": [],
        "chart": {"type": "bar", "x": "campaign", "y": ["sent"]},
    }
    assert provider.asked == ["narrative"]


def test_narrative_flags_a_number_that_is_not_in_the_data(scripted):
    scripted("Beta sent 2,500 emails, up 300% on last month.")

    resp = client.post("/analytics/narrative", json={"question": "q", "rows": [{"campaign": "Beta", "sent": 2500}]})

    assert resp.json()["ungrounded"] == ["300"]


def test_narrative_with_no_rows_skips_the_model(scripted):
    provider = scripted()

    resp = client.post("/analytics/narrative", json={"question": "q", "rows": []})

    assert "no data" in resp.json()["narrative"].lower()
    assert resp.json()["chart"] is None
    assert provider.prompts == []


def test_narrative_rejects_too_many_rows(scripted):
    scripted()

    resp = client.post("/analytics/narrative", json={"question": "q", "rows": [{"n": i} for i in range(201)]})

    assert resp.status_code == 422


# ---- /marketing/orchestrate/step (use case 3) ----

TOOLS = [
    {"name": "segment_size", "description": "Count members in a segment", "required": ["segment_id"]},
    {"name": "schedule_campaign", "description": "Schedule a campaign", "required": ["segment_id", "name"], "side_effect": True},
]


def test_orchestrate_step_proposes_a_read_tool(scripted):
    provider = scripted({"action": "tool", "tool": "segment_size", "args": {"segment_id": "s1"}})

    resp = client.post("/marketing/orchestrate/step", json={"goal": "size the segment", "tools": TOOLS})

    assert resp.status_code == 200
    assert resp.json() == {
        "kind": "tool", "tool": "segment_size", "args": {"segment_id": "s1"},
        "summary": None, "requires_approval": False, "reason": None,
    }
    assert provider.asked == ["orchestrator"]


def test_orchestrate_step_flags_a_side_effect_tool_for_approval(scripted):
    scripted({"action": "tool", "tool": "schedule_campaign", "args": {"segment_id": "s1", "name": "Winback"}})

    resp = client.post("/marketing/orchestrate/step", json={"goal": "g", "tools": TOOLS})

    assert resp.json()["requires_approval"] is True


def test_orchestrate_step_returns_finish(scripted):
    scripted({"action": "finish", "summary": "Segment s1 has 120 members."})

    resp = client.post("/marketing/orchestrate/step", json={"goal": "g", "tools": TOOLS})

    assert resp.json()["kind"] == "finish"
    assert resp.json()["summary"] == "Segment s1 has 120 members."


def test_orchestrate_step_reports_an_unknown_tool_as_an_error_step(scripted):
    scripted({"action": "tool", "tool": "delete_everything", "args": {}})

    resp = client.post("/marketing/orchestrate/step", json={"goal": "g", "tools": TOOLS})

    assert resp.status_code == 200
    assert resp.json()["kind"] == "error" and "unknown tool" in resp.json()["reason"]


def test_orchestrate_step_sends_earlier_observations_to_the_model(scripted):
    provider = scripted({"action": "finish", "summary": "ok"})
    steps = [{"tool": "segment_size", "args": {"segment_id": "s1"}, "observation": '{"members": 120}', "executed": True}]

    client.post("/marketing/orchestrate/step", json={"goal": "g", "tools": TOOLS, "steps": steps})

    assert '{"members": 120}' in provider.prompts[0]


@pytest.mark.parametrize(
    "tools",
    [
        [],
        [TOOLS[0], TOOLS[0]],
        [{"name": "bad name!", "description": "d"}],
    ],
    ids=["no_tools", "duplicate_names", "bad_name"],
)
def test_orchestrate_step_rejects_invalid_tool_lists(scripted, tools):
    scripted()

    assert client.post("/marketing/orchestrate/step", json={"goal": "g", "tools": tools}).status_code == 422


# ---- /tickets/classify (use case 6) ----


def test_tickets_are_classified_in_order_with_their_ids(scripted):
    provider = scripted(
        {"urgency": "high", "sentiment": "negative", "team": "billing"},
        {"urgency": "low", "sentiment": "neutral", "team": "account"},
    )
    tickets = [{"id": "FIXTURE-1", "text": "FIXTURE: charged twice"}, {"id": "FIXTURE-2", "text": "FIXTURE: change my email"}]

    resp = client.post("/tickets/classify", json={"tickets": tickets})

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["ticket_id"] for r in results] == ["FIXTURE-1", "FIXTURE-2"]
    assert results[0]["team"] == "billing" and results[0]["needs_review"] is False
    assert provider.asked == ["tickets"]  # one provider per request, reused for every ticket


def test_a_label_outside_the_allowed_set_is_flagged_for_review(scripted):
    scripted({"urgency": "apocalyptic", "sentiment": "negative", "team": "billing"})

    resp = client.post("/tickets/classify", json={"tickets": [{"id": "FIXTURE-1", "text": "FIXTURE: help"}]})

    result = resp.json()["results"][0]
    assert result["needs_review"] is True and result["urgency"] is None


def test_a_custom_team_list_is_enforced(scripted):
    scripted({"urgency": "low", "sentiment": "neutral", "team": "billing"})

    resp = client.post(
        "/tickets/classify", json={"tickets": [{"text": "FIXTURE: help"}], "teams": ["sales", "support"]}
    )

    assert resp.json()["results"][0]["needs_review"] is True


def test_too_many_tickets_are_rejected(scripted):
    scripted()

    assert client.post("/tickets/classify", json={"tickets": [{"text": "x"}] * 51}).status_code == 422


# ---- /onboarding/map-schema (use case 8) ----

TARGET = {"primary_email": "primary_email: the customer's main email address"}
SOURCE = [{"name": "cust_email", "samples": ["a@example.test"]}]
MAPPED = {"mappings": [{"source": "cust_email", "target": "primary_email", "confidence": 0.95, "reason": "r"}]}


def test_schema_mapping_returns_proposals(scripted):
    provider = scripted(MAPPED)

    resp = client.post("/onboarding/map-schema", json={"source_columns": SOURCE, "target_schema": TARGET})

    assert resp.status_code == 200
    mapping = resp.json()["mappings"][0]
    assert (mapping["source"], mapping["target"], mapping["status"]) == ("cust_email", "primary_email", "mapped")
    assert provider.asked == ["schema_mapping"]


def test_sample_values_are_not_sent_to_the_model_unless_asked(scripted):
    provider = scripted(MAPPED, MAPPED)

    client.post("/onboarding/map-schema", json={"source_columns": SOURCE, "target_schema": TARGET})
    client.post("/onboarding/map-schema", json={"source_columns": SOURCE, "target_schema": TARGET, "include_samples": True})

    assert "a@example.test" not in provider.prompts[0]
    assert "a@example.test" in provider.prompts[1]


def test_schema_mapping_unparseable_model_output_is_a_502(scripted):
    scripted("not json")

    resp = client.post("/onboarding/map-schema", json={"source_columns": SOURCE, "target_schema": TARGET})

    assert resp.status_code == 502


# ---- /marketing/draft-copy (use case 4) ----

COPY = {
    "subject": "We saved your spot", "preview_text": "A quick note",
    "email_body": "Hi {{first_name}}, your plan is here.\n{{unsubscribe_url}}", "social_caption": "Your plan is waiting.",
}
BRIEF = {"objective": "win back lapsed customers", "audience": "inactive for 90 days"}


def test_copy_draft_returns_all_fields_and_checks(scripted):
    provider = scripted(COPY)

    resp = client.post("/marketing/draft-copy", json={"brief": BRIEF, "guidelines": ["Plain, warm voice."]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["subject"] == "We saved your spot" and body["blocked"] is False
    assert {c["rule"] for c in body["checks"]} == {"placeholders", "length", "forbidden_claims", "buzzwords", "html_safety"}
    assert provider.asked == ["copy"]


def test_copy_draft_with_unsafe_html_is_blocked(scripted):
    scripted({**COPY, "email_body": "Hi {{first_name}}<script>x()</script>\n{{unsubscribe_url}}"})

    resp = client.post("/marketing/draft-copy", json={"brief": BRIEF, "guidelines": []})

    assert resp.json()["blocked"] is True


def test_copy_draft_rejects_an_oversized_brief(scripted):
    scripted()

    resp = client.post("/marketing/draft-copy", json={"brief": {"objective": "x" * 1001, "audience": "y"}, "guidelines": []})

    assert resp.status_code == 422


# ---- /support/answer (use case 1) ----

DOMAIN = "help.example.test"
KB = [{"url": f"https://{DOMAIN}/passwords", "title": "Passwords", "text": "Reset it in Settings."}]


def test_support_answer_from_the_knowledge_base(scripted):
    provider = scripted("Reset it in Settings.")

    resp = client.post("/support/answer", json={"question": "How do I reset my password?", "kb_passages": KB})

    assert resp.status_code == 200
    assert resp.json() == {
        "answer": "Reset it in Settings.", "found": True, "source_kind": "kb", "sources": [f"https://{DOMAIN}/passwords"],
    }
    assert provider.asked == ["support"]


def test_support_not_found_gives_no_invented_answer(scripted):
    scripted("NOT_FOUND_ANSWERS")

    resp = client.post("/support/answer", json={"question": "What is the refund policy?", "kb_passages": KB})

    assert resp.json()["found"] is False and resp.json()["source_kind"] is None


def test_support_web_fallback_drops_off_domain_passages(scripted):
    provider = scripted("NOT_FOUND_ANSWERS", "From the allowed page.")
    web = [
        {"url": "https://evil.example.test/a", "title": "x", "text": "bad"},
        {"url": f"https://{DOMAIN}/refunds", "title": "Refunds", "text": "Refunds take 5 days."},
    ]

    resp = client.post(
        "/support/answer",
        json={"question": "refund time?", "kb_passages": KB, "web_passages": web, "allowed_domain": DOMAIN},
    )

    assert resp.json()["sources"] == [f"https://{DOMAIN}/refunds"]
    assert "evil" not in provider.prompts[1]


def test_support_rejects_a_passage_url_that_is_not_http(scripted):
    scripted()

    bad = [{"url": "javascript:alert(1)", "title": "x", "text": "t"}]

    assert client.post("/support/answer", json={"question": "q", "kb_passages": bad}).status_code == 422


# ---- /persona/decide (use case 9) ----

EVIDENCE = {"persona": {"risk_level": "high"}, "features": {"churn_probability": 0.72}}
DECISION = {
    "perception": ["Churn probability is 0.72"], "interpretation": "At risk of leaving.",
    "action": {"code": RETENTION, "channel": "email", "rationale": "High churn probability."},
    "cited": ["features.churn_probability"],
}


def test_persona_decision_is_returned(scripted):
    provider = scripted(DECISION)

    resp = client.post("/persona/decide", json={"evidence": EVIDENCE})

    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == RETENTION and body["needs_review"] is False
    assert body["cited"] == ["features.churn_probability"]
    assert provider.asked == ["persona"]


def test_persona_action_outside_the_allowed_list_needs_review(scripted):
    scripted({**DECISION, "action": {"code": "Offer a 90% discount", "channel": "sms", "rationale": "x"}})

    resp = client.post("/persona/decide", json={"evidence": EVIDENCE})

    assert resp.status_code == 200
    assert resp.json()["needs_review"] is True and resp.json()["action"] is None


def test_persona_empty_evidence_skips_the_model(scripted):
    provider = scripted()

    resp = client.post("/persona/decide", json={"evidence": {}})

    assert resp.json()["needs_review"] is True
    assert provider.prompts == []


def test_persona_suppressed_actions_are_respected(scripted):
    scripted(DECISION)

    resp = client.post("/persona/decide", json={"evidence": EVIDENCE, "suppressed": [RETENTION]})

    assert resp.json()["needs_review"] is True and "suppressed" in resp.json()["reason"]


@pytest.mark.parametrize("key", ["email", "phone_number", "full_name", "first_name", "last_name", "national_id", "address"])
def test_persona_evidence_with_a_direct_identifier_is_rejected(scripted, key):
    scripted()

    resp = client.post("/persona/decide", json={"evidence": {"persona": {"risk_level": "high", key: "x"}}})

    assert resp.status_code == 422


def test_persona_oversized_evidence_is_rejected(scripted):
    scripted()

    resp = client.post("/persona/decide", json={"evidence": {"features": {"blob": "z" * 40_000}}})

    assert resp.status_code == 422


# ---- auth and model selection apply to every route ----

BODIES = {
    "/analytics/sql": {"question": "how many segments", "schema": SCHEMA},
    "/analytics/narrative": {"question": "q", "rows": [{"n": 1}]},
    "/marketing/orchestrate/step": {"goal": "g", "tools": TOOLS},
    "/tickets/classify": {"tickets": [{"text": "FIXTURE: help"}]},
    "/onboarding/map-schema": {"source_columns": SOURCE, "target_schema": TARGET},
    "/marketing/draft-copy": {"brief": BRIEF, "guidelines": []},
    "/support/answer": {"question": "q", "kb_passages": KB},
    "/persona/decide": {"evidence": EVIDENCE},
}


@pytest.mark.parametrize("path", sorted(BODIES))
def test_every_route_requires_the_bearer_token_when_one_is_configured(scripted, path):
    scripted()

    with patch.object(app_module.settings, "api_token", "s3cret"):
        assert client.post(path, json=BODIES[path]).status_code == 401
        assert client.post(path, json=BODIES[path], headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_a_correct_token_is_accepted(scripted):
    scripted({"urgency": "low", "sentiment": "neutral", "team": "general"})

    with patch.object(app_module.settings, "api_token", "s3cret"):
        resp = client.post("/tickets/classify", json=BODIES["/tickets/classify"], headers={"Authorization": "Bearer s3cret"})

    assert resp.status_code == 200


def test_provider_for_uses_the_use_case_model_override():
    with patch.object(app_module.settings, "llm_model", "default/model"), patch.object(
        app_module.settings, "llm_use_case_models", {"tickets": "small/model"}
    ):
        assert use_case_routes.provider_for("tickets").state.model == "small/model"
        assert use_case_routes.provider_for("sql").state.model == "default/model"
