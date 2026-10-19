"""The schema-doc loader and the role runner of ask-data, on the seeded
`customer360_agents_test` database. Skipped unless ASK_DATA_TEST_DATABASE_URL is set."""

import os
import uuid

import pytest

sa = pytest.importorskip("sqlalchemy")

from core.repositories.ask_data_repository import (  # noqa: E402
    MAX_ROWS,
    AskDataQueryError,
    AskDataRepository,
    AskDataUnavailable,
    clear_docs_cache,
    load_schema_docs,
    run_as_role,
)
from leo_customer360_agent.agents import NarrativeResult, SqlProposal  # noqa: E402

URL = os.environ.get("ASK_DATA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="ASK_DATA_TEST_DATABASE_URL not set")

ROLE = "customer360_ask_data"
DEMO_TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_TENANT = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture(scope="module")
def engine():
    assert URL.rstrip("/").endswith("customer360_agents_test"), "refusing to run outside the test database"
    eng = sa.create_engine(URL, future=True)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _fresh_docs_cache():
    clear_docs_cache()


def _count(engine, tenant, table):
    rows, _ = run_as_role(engine, ROLE, tenant, f"SELECT COUNT(*) AS n FROM customer360.{table}")
    return rows[0]["n"]


# ---- schema docs ----


def test_docs_list_only_what_the_role_can_read(engine):
    docs = load_schema_docs(engine, ROLE)

    assert "lifecycle_stage" in docs["cdp_master_profiles"]
    assert "churn_probability" in docs["cdp_master_profiles"]
    for hidden in ("email", "phone_number", "full_name", "address", "date_of_birth"):
        assert hidden not in docs["cdp_master_profiles"].replace("cdp_master_profiles", "")
    for table in ("sys_user", "crm_contact", "cdp_persona_features", "crm_connector_config"):
        assert table not in docs


def test_docs_carry_the_schema_authors_table_comments(engine):
    assert "golden/resolved customer profile" in load_schema_docs(engine, ROLE)["cdp_master_profiles"]


def test_docs_are_cached_between_calls(engine):
    assert load_schema_docs(engine, ROLE) is load_schema_docs(engine, ROLE)


def test_docs_for_a_role_that_does_not_exist_are_unavailable(engine):
    with pytest.raises(AskDataUnavailable):
        load_schema_docs(engine, "no_such_role")


def test_a_role_name_that_is_not_an_identifier_is_unavailable(engine):
    with pytest.raises(AskDataUnavailable):
        load_schema_docs(engine, 'x"; DROP TABLE y; --')


# ---- running SQL as the role ----


def test_the_tenants_own_rows_are_returned(engine):
    assert _count(engine, DEMO_TENANT, "cdp_master_profiles") == 700


def test_another_tenant_gets_no_rows(engine):
    assert _count(engine, OTHER_TENANT, "cdp_master_profiles") == 0


def test_results_are_cut_at_the_row_cap_and_flagged(engine):
    rows, truncated = run_as_role(engine, ROLE, DEMO_TENANT, "SELECT generate_series(1, 1000) AS n")

    assert len(rows) == MAX_ROWS and truncated is True
    assert rows[0] == {"n": 1} and rows[-1] == {"n": MAX_ROWS}


def test_a_short_result_is_not_flagged(engine):
    rows, truncated = run_as_role(engine, ROLE, DEMO_TENANT, "SELECT generate_series(1, 5) AS n")

    assert len(rows) == 5 and truncated is False


def test_a_trailing_sql_comment_is_harmless(engine):
    rows, _ = run_as_role(engine, ROLE, DEMO_TENANT, "SELECT COUNT(*) AS n FROM customer360.cdp_segments -- note")

    assert rows[0]["n"] >= 1


def test_text_that_tries_to_close_a_wrapper_is_just_a_syntax_error(engine):
    """The statement runs exactly as written, so there is no wrapper to break out of."""
    with pytest.raises(AskDataQueryError):
        run_as_role(engine, ROLE, DEMO_TENANT, "SELECT 1) AS q UNION ALL SELECT COUNT(*) FROM customer360.cdp_segments --")


def test_a_personal_column_is_refused_by_postgres(engine):
    with pytest.raises(AskDataQueryError, match="permission denied"):
        run_as_role(engine, ROLE, DEMO_TENANT, "SELECT email FROM customer360.cdp_master_profiles")


def test_a_write_is_refused_and_nothing_changes(engine):
    before = _count(engine, DEMO_TENANT, "cdp_segments")

    with pytest.raises(AskDataQueryError):
        run_as_role(engine, ROLE, DEMO_TENANT, "DELETE FROM customer360.cdp_segments")

    assert _count(engine, DEMO_TENANT, "cdp_segments") == before


def test_a_slow_statement_is_cancelled_by_the_timeout(engine):
    with pytest.raises(AskDataQueryError, match="timeout"):
        run_as_role(engine, ROLE, DEMO_TENANT, "SELECT COUNT(*) FROM generate_series(1, 2000000000)", timeout_ms=50)


def test_a_missing_role_makes_the_feature_unavailable(engine):
    with pytest.raises(AskDataUnavailable):
        run_as_role(engine, "no_such_role", DEMO_TENANT, "SELECT 1")


def test_a_role_that_could_bypass_row_level_security_is_refused(engine):
    with engine.connect() as conn:
        login = conn.execute(sa.text("SELECT current_user")).scalar()
        is_super = conn.execute(sa.text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar()
    assert is_super, "this test needs a superuser login to be meaningful"

    with pytest.raises(AskDataUnavailable, match="bypass row-level security"):
        run_as_role(engine, login, DEMO_TENANT, "SELECT COUNT(*) FROM customer360.cdp_master_profiles")


# ---- the repository on the real database, with a fake agent ----


class _Bind:
    """Just enough of a Session for the repository: it only needs get_bind()."""

    def __init__(self, engine):
        self._engine = engine

    def get_bind(self):
        return self._engine


class _Agent:
    def __init__(self, sql):
        self.sql = sql
        self.schema_seen = None

    def propose_sql(self, question, schema, history=None, k=3):
        self.schema_seen = schema
        return SqlProposal("ok", self.sql)

    def narrate_rows(self, question, rows):
        return NarrativeResult(f"Found {rows[0]['n']}.", [], None)


def test_the_repository_answers_from_the_real_database(engine):
    agent = _Agent("SELECT COUNT(*) AS n FROM customer360.cdp_master_profiles")
    repo = AskDataRepository(_Bind(engine), ROLE, agent=agent)

    result = repo.ask(DEMO_TENANT, "how many profiles")

    assert result.status == "ok" and result.rows == [{"n": 700}] and result.narrative == "Found 700."
    assert "email" not in agent.schema_seen["cdp_master_profiles"].replace("cdp_master_profiles", "")


def test_the_repository_reports_a_personal_column_request_as_an_error_result(engine):
    repo = AskDataRepository(_Bind(engine), ROLE, agent=_Agent("SELECT email AS n FROM customer360.cdp_master_profiles"))

    result = repo.ask(DEMO_TENANT, "list emails")

    assert result.status == "error" and "permission denied" in result.error and result.rows is None


def test_the_repository_gives_another_tenant_an_empty_answer(engine):
    repo = AskDataRepository(_Bind(engine), ROLE, agent=_Agent("SELECT COUNT(*) AS n FROM customer360.cdp_master_profiles"))

    result = repo.ask(OTHER_TENANT, "how many profiles")

    assert result.status == "ok" and result.rows == [{"n": 0}]
