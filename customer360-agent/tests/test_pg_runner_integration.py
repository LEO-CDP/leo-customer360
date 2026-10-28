"""Runs the guarded SQL runner against the seeded `customer360_agents_test`
database. Skipped unless AGENT_TEST_DATABASE_URL is set, so the default suite
stays hermetic (see run_unit_tests.sh).

The dev superuser bypasses row-level security, so isolation is only real when
the runner switches to a restricted role. The module creates a throwaway
SELECT-only role for that and drops it afterwards."""

import os

import pytest

pytest.importorskip("sqlalchemy")

from use_cases.pg_runner import make_pg_runner  # noqa: E402

URL = os.environ.get("AGENT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="AGENT_TEST_DATABASE_URL not set")

DEMO_TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
ROLE = "agent_readonly_test"
COUNT = "SELECT COUNT(*) AS n FROM customer360.cdp_master_profiles"


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine, text

    assert URL.rstrip("/").endswith("customer360_agents_test"), "refusing to run outside the test database"
    eng = create_engine(URL, future=True)
    with eng.begin() as conn:
        conn.execute(text(f"DROP OWNED BY {ROLE}")) if conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROLE}
        ).first() else None
        conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))
        conn.execute(text(f"CREATE ROLE {ROLE} NOLOGIN"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA customer360 TO {ROLE}"))
        conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA customer360 TO {ROLE}"))
    yield eng
    with eng.begin() as conn:
        conn.execute(text(f"DROP OWNED BY {ROLE}"))
        conn.execute(text(f"DROP ROLE {ROLE}"))
    eng.dispose()


def test_counts_seeded_master_profiles(engine):
    run = make_pg_runner(engine, DEMO_TENANT, role=ROLE)

    assert run(COUNT) == [{"n": 700}]


def test_other_tenant_sees_no_rows(engine):
    run = make_pg_runner(engine, OTHER_TENANT, role=ROLE)

    assert run(COUNT) == [{"n": 0}]


def test_runner_fails_closed_when_the_connection_bypasses_rls(engine):
    run = make_pg_runner(engine, OTHER_TENANT)

    with pytest.raises(RuntimeError, match="bypass row-level security"):
        run(COUNT)


def test_database_refuses_writes_even_without_the_guard(engine):
    run = make_pg_runner(engine, DEMO_TENANT, role=ROLE)

    with pytest.raises(Exception, match="read-only"):
        run("DELETE FROM customer360.cdp_master_profiles")

    assert make_pg_runner(engine, DEMO_TENANT, role=ROLE)(COUNT) == [{"n": 700}]


def test_schema_docs_loader_reads_the_real_schema_and_its_comments(engine):
    import re

    from use_cases.schema_docs import load_schema_docs

    docs = load_schema_docs(make_pg_runner(engine, DEMO_TENANT, role=ROLE))

    assert len(docs) >= 60
    assert docs["cdp_master_profiles"].startswith("cdp_master_profiles: The golden/resolved customer profile")
    assert "graph_edges" in docs, "a partitioned parent table should be described"
    assert not [t for t in docs if re.match(r"^graph_edges_", t)], "partition children leaked in"


def test_every_table_in_the_schema_carries_a_comment(engine):
    run = make_pg_runner(engine, DEMO_TENANT, role=ROLE)

    rows = run(
        "SELECT c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'customer360' AND c.relkind IN ('r', 'p') AND NOT c.relispartition "
        "AND obj_description(c.oid, 'pg_class') IS NULL ORDER BY 1"
    )

    assert [r["t"] for r in rows] == [], "tables without a COMMENT ON TABLE (retrieval cannot describe them)"


def test_tenant_id_must_be_a_uuid(engine):
    with pytest.raises(ValueError):
        make_pg_runner(engine, "x'; DROP TABLE y; --", role=ROLE)
