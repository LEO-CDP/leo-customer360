"""Use case 5: multi-turn "ask your data". Follow-up questions carry the
previous question and SQL, and each answer includes a chart-ready shape."""

from use_cases.analytics_chat import MAX_TURNS, AnalyticsChat, chart_spec

# Synthetic schema docs, test-only.
SCHEMA = {
    "cdp_master_profiles": "cdp_master_profiles: master_profile_id, lifecycle_stage, city",
    "cdp_segments": "cdp_segments: segment_id, segment_name, member_count",
}


class _Scripted:
    """Returns queued replies in order and records every prompt."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _chat(sql_replies, rows_by_call, narrative="Fine."):
    calls = iter(rows_by_call)
    sql_provider = _Scripted(*sql_replies)
    chat = AnalyticsChat(
        sql_provider=sql_provider,
        narrative_provider=_Scripted(*([narrative] * 20)),
        schema=SCHEMA,
        run_sql=lambda sql: next(calls),
    )
    return chat, sql_provider


def test_first_turn_returns_sql_rows_narrative_and_chart():
    chat, _ = _chat(
        ["SELECT lifecycle_stage, COUNT(*) AS n FROM cdp_master_profiles GROUP BY 1"],
        [[{"lifecycle_stage": "Active", "n": 5}, {"lifecycle_stage": "Churned", "n": 2}]],
        narrative="Active has 5 profiles.",
    )

    turn = chat.ask("How many profiles per lifecycle stage?")

    assert turn.sql.startswith("SELECT lifecycle_stage")
    assert turn.rows[0] == {"lifecycle_stage": "Active", "n": 5}
    assert turn.narrative == "Active has 5 profiles."
    assert turn.chart == {"type": "bar", "x": "lifecycle_stage", "y": ["n"]}


def test_follow_up_prompt_carries_previous_question_and_sql():
    chat, sql_provider = _chat(
        ["SELECT COUNT(*) AS n FROM cdp_master_profiles", "SELECT city, COUNT(*) AS n FROM cdp_master_profiles GROUP BY 1"],
        [[{"n": 7}], [{"city": "Hanoi", "n": 4}]],
    )

    chat.ask("How many profiles are there?")
    chat.ask("Now break that down by city")

    second_prompt = sql_provider.prompts[1]
    assert "How many profiles are there?" in second_prompt
    assert "SELECT COUNT(*) AS n FROM cdp_master_profiles" in second_prompt
    assert "Now break that down by city" in second_prompt


def test_failed_turn_is_not_kept_as_context():
    chat, sql_provider = _chat(
        ["DELETE FROM cdp_master_profiles", "SELECT COUNT(*) AS n FROM cdp_master_profiles"],
        [[{"n": 7}]],
    )

    failed = chat.ask("Delete every profile")
    chat.ask("How many profiles are there?")

    assert failed.error is not None
    assert failed.narrative is None
    assert "DELETE FROM" not in sql_provider.prompts[1]
    assert len(chat.history) == 1


def test_history_keeps_only_the_most_recent_turns():
    n = MAX_TURNS + 3
    chat, _ = _chat(["SELECT 1 AS n"] * n, [[{"n": 1}]] * n)

    for i in range(n):
        chat.ask(f"how many profiles, round {i}")

    assert len(chat.history) == MAX_TURNS
    assert chat.history[-1].question == f"how many profiles, round {n - 1}"


def test_question_the_schema_cannot_answer_gets_a_plain_reply():
    chat, _ = _chat(["NOT_FOUND_ANSWERS"], [])

    turn = chat.ask("What is the weather?")

    assert turn.rows is None
    assert turn.chart is None
    assert "cannot answer" in turn.narrative.lower()


def test_question_matching_no_table_gets_the_plain_reply_without_a_model_call():
    chat, sql_provider = _chat(["SELECT 1"], [])

    turn = chat.ask("Who are our biggest spenders?")

    assert "cannot answer" in turn.narrative.lower()
    assert sql_provider.prompts == []
    assert chat.history == []


def test_follow_up_still_finds_its_tables_through_the_earlier_sql():
    chat, sql_provider = _chat(
        ["SELECT COUNT(*) AS n FROM cdp_master_profiles", "SELECT city, COUNT(*) AS n FROM cdp_master_profiles GROUP BY 1"],
        [[{"n": 7}], [{"city": "Hanoi", "n": 4}]],
    )

    chat.ask("How many profiles are there?")
    second = chat.ask("Now split that by where they live")

    assert second.error is None
    assert second.sql.startswith("SELECT city")
    assert len(sql_provider.prompts) == 2


def test_chart_spec_shapes():
    assert chart_spec([]) is None
    assert chart_spec([{"n": 7}]) == {"type": "metric", "y": ["n"]}
    assert chart_spec([{"day": "2026-10-01", "n": 1}, {"day": "2026-10-02", "n": 3}]) == {
        "type": "line", "x": "day", "y": ["n"],
    }
    assert chart_spec([{"a": "x", "b": "y"}, {"a": "p", "b": "q"}]) == {"type": "table"}
