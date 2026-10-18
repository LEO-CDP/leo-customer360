"""Use case 2: schema-grounded Text-to-SQL. The provider and the SQL runner are
injected, so nothing here touches a network or a database."""

from use_cases.text_to_sql import (
    NOT_FOUND,
    SQLProposal,
    TextToSQLResult,
    answer_question,
    generate_sql,
    select_relevant_tables,
)

# Fixture schema docs (synthetic, test-only). Keys are table names.
SCHEMA = {
    "cdp_master_profiles": "cdp_master_profiles: master_profile_id, lifecycle_stage, churn_risk_tier, clv_segment, city",
    "crm_transactions": "crm_transactions: transaction_id, master_profile_id, amount, currency, transaction_time",
    "cdp_segments": "cdp_segments: segment_id, segment_name, member_count",
    "crm_campaign": "crm_campaign: campaign_id, campaign_name, channel, status",
}


class _RecordingProvider:
    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def _runner(rows):
    calls: list[str] = []

    def run(sql: str):
        calls.append(sql)
        return rows

    run.calls = calls
    return run


def test_retrieval_ranks_tables_by_overlap_with_the_question():
    picked = select_relevant_tables("total transaction amount per profile", SCHEMA, k=2)

    assert picked[0] == "crm_transactions"
    assert len(picked) == 2


def test_prompt_contains_only_the_retrieved_tables():
    provider = _RecordingProvider("SELECT 1")

    answer_question("how many segments exist", provider, SCHEMA, _runner([{"n": 1}]), k=1)

    assert "cdp_segments" in provider.prompts[0]
    assert "crm_transactions" not in provider.prompts[0]


def test_happy_path_returns_sql_and_rows():
    provider = _RecordingProvider("```sql\nSELECT COUNT(*) AS n FROM cdp_segments\n```")
    run = _runner([{"n": 8}])

    result = answer_question("how many segments", provider, SCHEMA, run)

    assert result == TextToSQLResult(sql="SELECT COUNT(*) AS n FROM cdp_segments", rows=[{"n": 8}], error=None)
    assert run.calls == ["SELECT COUNT(*) AS n FROM cdp_segments"]


def test_not_found_sentinel_skips_execution():
    run = _runner([])

    result = answer_question("what is the weather", _RecordingProvider(NOT_FOUND), SCHEMA, run)

    assert result.sql == NOT_FOUND
    assert result.rows is None
    assert run.calls == []


def test_write_statement_is_refused_and_never_executed():
    run = _runner([])

    result = answer_question(
        "remove all master profiles", _RecordingProvider("DELETE FROM cdp_master_profiles"), SCHEMA, run
    )

    assert result.rows is None
    assert "SELECT" in result.error
    assert run.calls == []


def test_execution_failure_is_reported_not_raised():
    def failing(sql: str):
        raise RuntimeError('column "nope" does not exist')

    result = answer_question("list segments", _RecordingProvider("SELECT nope FROM cdp_segments"), SCHEMA, failing)

    assert result.rows is None
    assert 'column "nope" does not exist' in result.error


# ---- zero-match behaviour (found by the retrieval evaluation) ----


def test_require_match_drops_tables_that_share_no_words_with_the_question():
    assert select_relevant_tables("what is the weather", SCHEMA, k=3, require_match=True) == []


def test_require_match_returns_only_the_matching_tables_even_when_k_is_larger():
    picked = select_relevant_tables("which campaign channel", SCHEMA, k=3, require_match=True)

    assert picked == ["crm_campaign"]


def test_default_retrieval_still_pads_to_k_for_callers_that_rely_on_it():
    assert len(select_relevant_tables("what is the weather", SCHEMA, k=3)) == 3


def test_question_matching_no_table_never_reaches_the_model_or_the_database():
    provider = _RecordingProvider("SELECT 1")
    run = _runner([{"n": 1}])

    result = answer_question("Who are our biggest spenders", provider, SCHEMA, run)

    assert result == TextToSQLResult(sql=NOT_FOUND, rows=None, error=None)
    assert provider.prompts == []
    assert run.calls == []


def test_prompt_is_not_padded_with_unrelated_tables_when_k_is_larger_than_the_matches():
    provider = _RecordingProvider("SELECT 1")

    answer_question("which campaign channel", provider, SCHEMA, _runner([{"n": 1}]), k=3)

    assert "crm_campaign" in provider.prompts[0]
    for unrelated in ("crm_transactions", "cdp_segments", "cdp_master_profiles"):
        assert unrelated not in provider.prompts[0]


# ---- generate_sql: a proposal only, nothing is executed (stateless endpoint) ----


def test_generate_sql_returns_a_cleaned_proposal():
    provider = _RecordingProvider("```sql\nSELECT COUNT(*) AS n FROM cdp_segments;\n```")

    proposal = generate_sql("how many segments", provider, SCHEMA)

    assert proposal == SQLProposal(status="ok", sql="SELECT COUNT(*) AS n FROM cdp_segments", error=None)


def test_generate_sql_reports_not_found_from_the_model():
    proposal = generate_sql("how many segments", _RecordingProvider(NOT_FOUND), SCHEMA)

    assert proposal == SQLProposal(status="not_found", sql=None, error=None)


def test_generate_sql_reports_not_found_without_a_model_call_when_no_table_matches():
    provider = _RecordingProvider("SELECT 1")

    proposal = generate_sql("who are our biggest spenders", provider, SCHEMA)

    assert proposal.status == "not_found"
    assert provider.prompts == []


def test_generate_sql_refuses_a_write_and_keeps_the_raw_text_for_the_caller_to_log():
    proposal = generate_sql("remove all segments", _RecordingProvider("DELETE FROM cdp_segments"), SCHEMA)

    assert proposal.status == "refused"
    assert proposal.sql == "DELETE FROM cdp_segments"
    assert "SELECT" in proposal.error


def test_generate_sql_carries_earlier_turns_into_the_prompt_and_retrieval():
    provider = _RecordingProvider("SELECT 1")
    history = [{"question": "how many segments", "sql": "SELECT COUNT(*) FROM cdp_segments"}]

    proposal = generate_sql("now split that by status", provider, SCHEMA, history=history)

    assert proposal.status == "ok"
    assert "how many segments" in provider.prompts[0]
    assert "SELECT COUNT(*) FROM cdp_segments" in provider.prompts[0]
    assert "cdp_segments:" in provider.prompts[0]


def test_function_words_do_not_count_as_a_match():
    schema = {
        "crm_flow_edge": "crm_flow_edge: from_node, to_node, branch",
        "cdp_segments": SCHEMA["cdp_segments"],
    }

    assert select_relevant_tables("Where does the traffic come from?", schema, k=3, require_match=True) == []


def test_function_words_do_not_hide_a_real_match():
    picked = select_relevant_tables("how many of the segments exist from last week", SCHEMA, k=1, require_match=True)

    assert picked == ["cdp_segments"]


def test_a_plain_english_description_in_the_doc_lets_a_paraphrase_match():
    described = dict(SCHEMA)
    described["crm_transactions"] = (
        "crm_transactions: customer purchases and revenue. Columns: transaction_id, amount, currency, channel"
    )

    picked = select_relevant_tables("How much revenue came from the web?", described, k=3, require_match=True)

    assert picked[0] == "crm_transactions"
