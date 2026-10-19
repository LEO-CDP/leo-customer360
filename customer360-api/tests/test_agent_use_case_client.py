"""Client functions for the customer360-agent use-case routes. `_post` is replaced,
so these cover the request each function sends, the dataclass it returns, and the
error when the agent's reply lacks a required field."""

import pytest

from leo_customer360_agent import agents, client
from leo_customer360_agent.client import AIProviderError


class _Agent:
    """Stands in for client._post: records calls and returns a canned reply."""

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path, payload):
        self.calls.append((path, payload))
        return self.reply


@pytest.fixture
def agent(monkeypatch):
    def install(reply):
        fake = _Agent(reply)
        monkeypatch.setattr(client, "_post", fake)
        return fake

    return install


SCHEMA = {"cdp_segments": "cdp_segments: Saved audience groups. Columns: segment_id"}


def test_propose_sql_sends_question_schema_history_and_k(agent):
    fake = agent({"status": "ok", "sql": "SELECT 1", "error": None})
    history = [{"question": "earlier", "sql": "SELECT 0"}]

    proposal = agents.propose_sql("how many segments", SCHEMA, history=history, k=4)

    assert fake.calls == [
        ("/analytics/sql", {"question": "how many segments", "schema": SCHEMA, "history": history, "k": 4})
    ]
    assert (proposal.status, proposal.sql, proposal.error) == ("ok", "SELECT 1", None)


def test_propose_sql_defaults_to_no_history(agent):
    fake = agent({"status": "not_found", "sql": None, "error": None})

    proposal = agents.propose_sql("q", SCHEMA)

    assert fake.calls[0][1]["history"] == []
    assert proposal.status == "not_found" and proposal.sql is None


def test_narrate_rows_returns_text_ungrounded_numbers_and_chart(agent):
    fake = agent({"narrative": "Beta led.", "ungrounded": ["300"], "chart": {"type": "bar", "x": "c", "y": ["n"]}})

    result = agents.narrate_rows("how did we do", [{"c": "Beta", "n": 2}])

    assert fake.calls == [("/analytics/narrative", {"question": "how did we do", "rows": [{"c": "Beta", "n": 2}]})]
    assert result.narrative == "Beta led." and result.ungrounded == ["300"] and result.chart["type"] == "bar"


def test_narrate_rows_chart_may_be_missing(agent):
    agent({"narrative": "No data.", "ungrounded": []})

    assert agents.narrate_rows("q", []).chart is None


def test_plan_next_step_sends_tool_specs_and_steps(agent):
    fake = agent({"kind": "tool", "tool": "segment_size", "args": {"segment_id": "s1"}, "requires_approval": False})
    tools = [{"name": "segment_size", "description": "Count members", "required": ["segment_id"]}]
    steps = [{"tool": "x", "args": {}, "observation": "ok", "executed": True}]

    step = agents.plan_next_step("size it", tools, steps=steps, context=["note"])

    assert fake.calls == [
        ("/marketing/orchestrate/step", {"goal": "size it", "tools": tools, "steps": steps, "context": ["note"]})
    ]
    assert (step.kind, step.tool, step.args, step.requires_approval) == ("tool", "segment_size", {"segment_id": "s1"}, False)


def test_plan_next_step_finish_and_error_shapes(agent):
    agent({"kind": "finish", "summary": "Done."})
    assert agents.plan_next_step("g", []).summary == "Done."

    agent({"kind": "error", "reason": "unknown tool"})
    assert agents.plan_next_step("g", []).reason == "unknown tool"


def test_classify_tickets_returns_one_label_per_ticket(agent):
    fake = agent({"results": [
        {"ticket_id": "T1", "urgency": "high", "sentiment": "negative", "team": "billing", "needs_review": False, "reason": None},
        {"ticket_id": "T2", "urgency": None, "sentiment": None, "team": None, "needs_review": True, "reason": "bad label"},
    ]})

    labels = agents.classify_tickets([{"id": "T1", "text": "a"}, {"id": "T2", "text": "b"}], teams=["billing", "sales"])

    assert fake.calls[0][0] == "/tickets/classify"
    assert fake.calls[0][1]["teams"] == ["billing", "sales"]
    assert [l.ticket_id for l in labels] == ["T1", "T2"]
    assert labels[0].team == "billing" and labels[1].needs_review is True


def test_classify_tickets_omits_teams_unless_given(agent):
    fake = agent({"results": []})

    agents.classify_tickets([{"text": "a"}])

    assert "teams" not in fake.calls[0][1]


def test_propose_field_mappings_sends_samples_flag_and_returns_proposals(agent):
    fake = agent({"mappings": [
        {"source": "cust_email", "target": "primary_email", "suggested": "primary_email", "confidence": 0.95,
         "status": "mapped", "reason": None},
    ]})

    proposals = agents.propose_field_mappings(
        [{"name": "cust_email"}], {"primary_email": "primary_email: main email"}, include_samples=True, max_targets=10
    )

    assert fake.calls[0][0] == "/onboarding/map-schema"
    assert fake.calls[0][1]["include_samples"] is True and fake.calls[0][1]["max_targets"] == 10
    assert (proposals[0].source, proposals[0].target, proposals[0].status) == ("cust_email", "primary_email", "mapped")


def test_draft_marketing_copy_returns_fields_checks_and_blocked_flag(agent):
    fake = agent({
        "subject": "S", "preview_text": "P", "email_body": "B", "social_caption": "C",
        "checks": [{"rule": "html_safety", "passed": False, "detail": "<script", "blocking": True}], "blocked": True,
    })

    draft = agents.draft_marketing_copy({"objective": "win back", "audience": "lapsed"}, ["Plain voice."])

    assert fake.calls[0][0] == "/marketing/draft-copy"
    assert fake.calls[0][1]["brief"] == {"objective": "win back", "audience": "lapsed"}
    assert draft.subject == "S" and draft.blocked is True
    assert draft.checks[0].rule == "html_safety" and draft.checks[0].blocking is True


def test_answer_support_question_returns_answer_and_sources(agent):
    fake = agent({"answer": "Use Settings.", "found": True, "source_kind": "kb", "sources": ["https://h.example.test/a"]})
    passages = [{"url": "https://h.example.test/a", "title": "T", "text": "x"}]

    answer = agents.answer_support_question("how?", passages, web_passages=passages, allowed_domain="h.example.test")

    assert fake.calls[0][0] == "/support/answer"
    assert fake.calls[0][1]["allowed_domain"] == "h.example.test" and fake.calls[0][1]["web_passages"] == passages
    assert (answer.found, answer.source_kind, answer.sources) == (True, "kb", ["https://h.example.test/a"])


def test_decide_next_best_action_returns_the_decision(agent):
    fake = agent({
        "perception": ["p"], "interpretation": "i", "action": "A", "channel": "email", "rationale": "r",
        "cited": ["features.x"], "needs_review": False, "reason": None,
    })

    decision = agents.decide_next_best_action({"features": {"x": 1}}, suppressed=["B"])

    assert fake.calls[0][0] == "/persona/decide"
    assert fake.calls[0][1]["suppressed"] == ["B"] and "allowed_actions" not in fake.calls[0][1]
    assert (decision.action, decision.channel, decision.cited, decision.needs_review) == ("A", "email", ["features.x"], False)


@pytest.mark.parametrize(
    "call, reply",
    [
        (lambda: agents.propose_sql("q", SCHEMA), {"sql": "SELECT 1"}),
        (lambda: agents.narrate_rows("q", []), {"ungrounded": []}),
        (lambda: agents.plan_next_step("g", []), {"tool": "x"}),
        (lambda: agents.classify_tickets([{"text": "a"}]), {}),
        (lambda: agents.propose_field_mappings([{"name": "c"}], {"f": "f"}), {}),
        (lambda: agents.draft_marketing_copy({"objective": "x"}, []), {"subject": "S"}),
        (lambda: agents.answer_support_question("q", []), {"answer": "A"}),
        (lambda: agents.decide_next_best_action({"features": {"x": 1}}), {"action": "A"}),
    ],
    ids=["sql", "narrative", "step", "tickets", "mapping", "copy", "support", "persona"],
)
def test_a_reply_missing_a_required_field_is_an_ai_provider_error(agent, call, reply):
    agent(reply)

    with pytest.raises(AIProviderError):
        call()


def test_agent_failures_pass_through_unchanged(monkeypatch):
    def boom(path, payload):
        raise AIProviderError("AI agent error: upstream timeout")

    monkeypatch.setattr(client, "_post", boom)

    with pytest.raises(AIProviderError, match="upstream timeout"):
        agents.propose_sql("q", SCHEMA)
