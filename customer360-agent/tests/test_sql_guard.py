"""The SELECT-only guard is the only thing standing between model-written SQL
and the database, so every rejection path is pinned here."""

import pytest

from leo_customer360_agent.sql_guard import UnsafeSQLError, ensure_select_only


def test_plain_select_passes_unchanged():
    assert ensure_select_only("SELECT COUNT(*) FROM customers") == "SELECT COUNT(*) FROM customers"


def test_trailing_semicolon_and_whitespace_are_stripped():
    assert ensure_select_only("  SELECT 1 ;\n") == "SELECT 1"


def test_cte_select_passes():
    sql = "WITH t AS (SELECT 1 AS n) SELECT n FROM t"
    assert ensure_select_only(sql) == sql


def test_markdown_fence_is_removed():
    assert ensure_select_only("```sql\nSELECT 1\n```") == "SELECT 1"


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM customers",
        "UPDATE customers SET email = NULL",
        "DROP TABLE customers",
        "INSERT INTO customers VALUES (1)",
        "TRUNCATE customers",
        "ALTER TABLE customers ADD COLUMN x int",
    ],
)
def test_non_select_statements_are_rejected(sql):
    with pytest.raises(UnsafeSQLError):
        ensure_select_only(sql)


def test_stacked_statements_are_rejected():
    with pytest.raises(UnsafeSQLError, match="single statement"):
        ensure_select_only("SELECT 1; DROP TABLE customers")


def test_write_keyword_hidden_inside_a_cte_is_rejected():
    with pytest.raises(UnsafeSQLError):
        ensure_select_only("WITH d AS (DELETE FROM customers RETURNING *) SELECT * FROM d")


def test_write_keyword_inside_a_string_literal_is_allowed():
    sql = "SELECT * FROM events WHERE event_name = 'delete_account'"
    assert ensure_select_only(sql) == sql


def test_column_named_like_a_keyword_substring_is_allowed():
    sql = "SELECT updated_at, deleted_flag FROM customers"
    assert ensure_select_only(sql) == sql


def test_sql_comments_cannot_smuggle_a_second_statement():
    with pytest.raises(UnsafeSQLError):
        ensure_select_only("SELECT 1 -- ok\n; DROP TABLE customers")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT set_config('app.tenant_id', 'someone-else', true)",
        "SELECT pg_catalog.set_config('role', 'postgres', true)",
        'SELECT "set_config"(\'app.tenant_id\', \'x\', true)',
        "SELECT SET_CONFIG('app.tenant_id', 'x', true)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_ls_dir('/')",
        "SELECT lo_import('/etc/passwd')",
        "SELECT * FROM dblink('host=other', 'select 1') AS t(a int)",
        "SELECT pg_sleep(60)",
        "SELECT pg_terminate_backend(1)",
        "SELECT pg_advisory_lock(1)",
        "SELECT pg_advisory_xact_lock(1)",
        "SELECT nextval('some_seq')",
        "SELECT query_to_xml('select * from other_table', true, false, '')",
    ],
)
def test_functions_that_change_session_state_or_reach_the_server_are_rejected(sql):
    with pytest.raises(UnsafeSQLError, match="function"):
        ensure_select_only(sql)


def test_a_function_name_inside_a_string_literal_is_allowed():
    sql = "SELECT * FROM audit WHERE action = 'set_config'"

    assert ensure_select_only(sql) == sql


def test_a_column_that_merely_contains_a_blocked_name_is_allowed():
    sql = "SELECT set_config_value, pg_sleep_seconds FROM settings"

    assert ensure_select_only(sql) == sql


def test_ordinary_aggregate_and_date_functions_are_allowed():
    sql = "SELECT date_trunc('day', created_at) AS d, COUNT(*), SUM(amount), AVG(amount) FROM t GROUP BY 1"

    assert ensure_select_only(sql) == sql


def test_empty_input_is_rejected():
    with pytest.raises(UnsafeSQLError, match="empty"):
        ensure_select_only("   ")
