"""Functional scenario on the seeded `customer360_agents_test` database: the
SQL-backed agents run against the real schema, comments and data, with scripted
model replies. Skipped unless AGENT_TEST_DATABASE_URL is set."""

import os

import pytest

pytest.importorskip("sqlalchemy")

from use_cases.analytics_chat import AnalyticsChat  # noqa: E402
from use_cases.campaign_orchestrator import Tool, run_orchestration  # noqa: E402
from use_cases.performance_narrative import narrate  # noqa: E402
from use_cases.pg_runner import make_pg_runner  # noqa: E402
from use_cases.schema_docs import load_schema_docs  # noqa: E402
from use_cases.text_to_sql import answer_question  # noqa: E402

URL = os.environ.get("AGENT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="AGENT_TEST_DATABASE_URL not set")

DEMO_TENANT = "11111111-1111-1111-1111-111111111111"
ROLE = "agent_readonly_scenario_test"
SEGMENT_COUNT = "SELECT COUNT(*) AS n FROM customer360.cdp_segments"
BY_STAGE = "SELECT lifecycle_stage, COUNT(*) AS n FROM customer360.cdp_master_profiles GROUP BY 1 ORDER BY 1"
BY_CHANNEL = "SELECT preferred_channel, COUNT(*) AS n FROM customer360.cdp_master_profiles GROUP BY 1 ORDER BY 1"


class _Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


@pytest.fixture(scope="module")
def runner():
    from sqlalchemy import create_engine, text

    assert URL.rstrip("/").endswith("customer360_agents_test"), "refusing to run outside the test database"
    engine = create_engine(URL, future=True)
    with engine.begin() as conn:
        conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))
        conn.execute(text(f"CREATE ROLE {ROLE} NOLOGIN"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA customer360 TO {ROLE}"))
        conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA customer360 TO {ROLE}"))
    yield make_pg_runner(engine, DEMO_TENANT, role=ROLE)
    with engine.begin() as conn:
        conn.execute(text(f"DROP OWNED BY {ROLE}"))
        conn.execute(text(f"DROP ROLE {ROLE}"))
    engine.dispose()


@pytest.fixture(scope="module")
def docs(runner):
    return load_schema_docs(runner)


def test_text_to_sql_answers_from_the_real_schema(runner, docs):
    provider = _Scripted(SEGMENT_COUNT)

    result = answer_question("how many segments are there", provider, docs, runner)

    assert result.error is None
    assert result.rows == runner(SEGMENT_COUNT)
    assert result.rows[0]["n"] >= 1
    assert "cdp_segments:" in provider.prompts[0]


def test_a_destructive_statement_never_reaches_the_data(runner, docs):
    before = runner(SEGMENT_COUNT)

    result = answer_question("remove all segments", _Scripted("DELETE FROM customer360.cdp_segments"), docs, runner)

    assert result.rows is None and "SELECT" in result.error
    assert runner(SEGMENT_COUNT) == before


def test_a_database_error_is_reported_and_the_next_query_still_works(runner, docs):
    result = answer_question("list segments", _Scripted("SELECT nope FROM customer360.cdp_segments"), docs, runner)

    assert result.rows is None and "nope" in result.error
    assert runner(SEGMENT_COUNT)[0]["n"] >= 1


def test_analytics_chat_follows_up_on_real_data(runner, docs):
    sql_provider = _Scripted(BY_STAGE, BY_CHANNEL)
    chat = AnalyticsChat(sql_provider, _Scripted("Counts are grouped.", "Counts are grouped."), docs, runner)

    first = chat.ask("How many profiles per lifecycle stage?")
    second = chat.ask("Now break that down by preferred channel")

    assert first.error is None and first.rows and first.chart["type"] == "bar"
    assert second.error is None and second.rows and second.chart["x"] == "preferred_channel"
    assert BY_STAGE in sql_provider.prompts[1]
    assert sum(r["n"] for r in first.rows) == sum(r["n"] for r in second.rows)


def test_narrative_over_real_rows_quotes_only_real_numbers(runner):
    rows = runner(BY_STAGE)
    biggest = max(r["n"] for r in rows)

    story = narrate("Profiles per lifecycle stage", rows, _Scripted(f"The largest group has {biggest} profiles."))

    assert story.ungrounded == []


def test_orchestrator_with_real_tools_stays_dry_run(runner):
    scheduled: list[tuple] = []

    def list_segments():
        return runner("SELECT segment_name, member_count FROM customer360.cdp_segments ORDER BY segment_name LIMIT 3")

    tools = {
        "list_segments": Tool("list_segments", "List a few segments", list_segments),
        "schedule_campaign": Tool(
            "schedule_campaign", "Schedule a campaign", lambda segment, name: scheduled.append((segment, name)),
            required=("segment", "name"), side_effect=True,
        ),
    }
    provider = _Scripted(
        '{"action": "tool", "tool": "list_segments", "args": {}}',
        '{"action": "tool", "tool": "schedule_campaign", "args": {"segment": "any", "name": "Winback"}}',
        '{"action": "finish", "summary": "Listed segments and planned a campaign."}',
    )

    result = run_orchestration("plan a winback campaign for a segment", tools, provider)

    first_segment = runner("SELECT segment_name FROM customer360.cdp_segments ORDER BY segment_name LIMIT 1")[0]["segment_name"]
    assert result.status == "finished"
    assert first_segment in result.steps[0].observation
    assert result.steps[1].executed is False and "dry run" in result.steps[1].observation
    assert scheduled == []
