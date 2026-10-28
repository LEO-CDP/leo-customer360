"""Orchestration of "ask your data": schema docs, the agent's proposal, the API's own
re-check of the SQL, execution as the restricted role, then the narrative. The agent,
the docs loader and the runner are fakes; the database behaviour has its own tests
(test_ask_data_role_integration.py, test_ask_data_db_integration.py)."""

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest

from core.repositories.ask_data_repository import (
    AskDataAgentError,
    AskDataQueryError,
    AskDataRepository,
    AskDataUnavailable,
)
from leo_customer360_agent.agents import NarrativeResult, SqlProposal
from leo_customer360_agent.client import AIProviderError

TENANT = uuid.uuid4()
DOCS = {"cdp_segments": "cdp_segments: Saved audience groups. Columns: segment_id, segment_name"}


class _Agent:
    """A fake agent client that records its calls and returns canned replies."""

    def __init__(self, proposal=None, narrative=None, propose_error=None, narrate_error=None):
        self.proposal = proposal or SqlProposal("ok", "SELECT COUNT(*) AS n FROM customer360.cdp_segments")
        self.narrative = narrative or NarrativeResult("There are 8 segments.", [], {"type": "metric", "y": ["n"]})
        self.propose_error = propose_error
        self.narrate_error = narrate_error
        self.propose_calls: list[dict] = []
        self.narrate_calls: list[dict] = []

    def propose_sql(self, question, schema, history=None, k=3):
        self.propose_calls.append({"question": question, "schema": schema, "history": history, "k": k})
        if self.propose_error:
            raise self.propose_error
        return self.proposal

    def narrate_rows(self, question, rows):
        self.narrate_calls.append({"question": question, "rows": rows})
        if self.narrate_error:
            raise self.narrate_error
        return self.narrative


class _Runner:
    def __init__(self, rows=None, truncated=False, error=None):
        self.rows = [{"n": 8}] if rows is None else rows
        self.truncated = truncated
        self.error = error
        self.calls: list[tuple] = []

    def __call__(self, tenant_id, sql):
        self.calls.append((tenant_id, sql))
        if self.error:
            raise self.error
        return self.rows, self.truncated


def _repo(agent=None, runner=None, docs=DOCS):
    agent = agent or _Agent()
    runner = runner or _Runner()
    repo = AskDataRepository(db=None, role="customer360_ask_data", agent=agent, docs_loader=lambda: docs, runner=runner)
    return repo, agent, runner


def test_ok_answer_has_sql_rows_narrative_and_chart():
    repo, agent, runner = _repo()

    result = repo.ask(TENANT, "how many segments are there")

    assert result.status == "ok"
    assert result.sql == "SELECT COUNT(*) AS n FROM customer360.cdp_segments"
    assert result.rows == [{"n": 8}] and result.truncated is False
    assert result.narrative == "There are 8 segments." and result.chart == {"type": "metric", "y": ["n"]}
    assert runner.calls == [(TENANT, "SELECT COUNT(*) AS n FROM customer360.cdp_segments")]
    assert agent.propose_calls[0]["schema"] == DOCS


def test_earlier_turns_are_passed_to_the_agent():
    repo, agent, _ = _repo()
    history = [{"question": "earlier", "sql": "SELECT 1"}]

    repo.ask(TENANT, "now split that", history=history)

    assert agent.propose_calls[0]["history"] == history


def test_not_found_never_runs_sql_or_narrates():
    repo, agent, runner = _repo(agent=_Agent(proposal=SqlProposal("not_found")))

    result = repo.ask(TENANT, "who are our biggest spenders")

    assert result.status == "not_found" and result.rows is None and result.sql is None
    assert runner.calls == [] and agent.narrate_calls == []


def test_a_refused_proposal_is_not_run_and_the_raw_text_is_not_returned():
    repo, _, runner = _repo(agent=_Agent(proposal=SqlProposal("refused", "DELETE FROM x", "Refused, only a single SELECT")))

    result = repo.ask(TENANT, "remove everything")

    assert result.status == "refused" and "SELECT" in result.error
    assert result.sql is None and "DELETE" not in str(result)
    assert runner.calls == []


@pytest.mark.parametrize(
    "unsafe",
    [
        "DELETE FROM customer360.cdp_segments",
        "SELECT set_config('app.tenant_id', 'x', true)",
        "SELECT 1; DROP TABLE customer360.cdp_segments",
        "SELECT pg_sleep(60)",
    ],
)
def test_sql_the_agent_calls_ok_is_checked_again_by_the_api(unsafe):
    repo, _, runner = _repo(agent=_Agent(proposal=SqlProposal("ok", unsafe)))

    result = repo.ask(TENANT, "q")

    assert result.status == "refused" and result.sql is None
    assert runner.calls == []


def test_a_refusal_by_the_database_is_reported_as_an_error_not_raised():
    runner = _Runner(error=AskDataQueryError("permission denied for table sys_user"))
    repo, agent, _ = _repo(runner=runner)

    result = repo.ask(TENANT, "show users")

    assert result.status == "error" and "permission denied" in result.error
    assert result.rows is None and agent.narrate_calls == []


def test_the_role_being_unusable_is_unavailable_not_an_error_result():
    repo, _, _ = _repo(runner=_Runner(error=AskDataUnavailable("role customer360_ask_data is not available")))

    with pytest.raises(AskDataUnavailable):
        repo.ask(TENANT, "q")


def test_no_readable_schema_is_unavailable():
    repo, agent, _ = _repo(docs={})

    with pytest.raises(AskDataUnavailable):
        repo.ask(TENANT, "q")

    assert agent.propose_calls == []


def test_an_agent_failure_while_proposing_is_an_agent_error():
    repo, _, runner = _repo(agent=_Agent(propose_error=AIProviderError("AI agent error: timeout")))

    with pytest.raises(AskDataAgentError, match="timeout"):
        repo.ask(TENANT, "q")

    assert runner.calls == []


def test_a_narrative_failure_still_returns_the_rows():
    repo, _, _ = _repo(agent=_Agent(narrate_error=AIProviderError("AI agent error: timeout")))

    result = repo.ask(TENANT, "q")

    assert result.status == "ok" and result.rows == [{"n": 8}]
    assert result.narrative is None and "timeout" in result.narrative_error


def test_truncated_results_are_flagged_and_still_narrated():
    repo, agent, _ = _repo(runner=_Runner(rows=[{"n": i} for i in range(200)], truncated=True))

    result = repo.ask(TENANT, "q")

    assert result.truncated is True and len(result.rows) == 200
    assert agent.narrate_calls


def test_rows_are_made_json_safe_before_they_reach_the_agent():
    rows = [{"amount": Decimal("12.50"), "id": uuid.UUID(int=7), "day": date(2026, 10, 1), "at": datetime(2026, 10, 1, 9, 30)}]
    repo, agent, _ = _repo(runner=_Runner(rows=rows))

    result = repo.ask(TENANT, "q")

    expected = {"amount": 12.5, "id": str(uuid.UUID(int=7)), "day": "2026-10-01", "at": "2026-10-01T09:30:00"}
    assert result.rows == [expected]
    assert agent.narrate_calls[0]["rows"] == [expected]


def test_no_rows_is_still_a_normal_answer():
    repo, agent, _ = _repo(runner=_Runner(rows=[]))

    result = repo.ask(TENANT, "q")

    assert result.status == "ok" and result.rows == []
    assert agent.narrate_calls[0]["rows"] == []
