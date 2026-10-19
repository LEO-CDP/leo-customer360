"""Use case 9 against the seeded `customer360_agents_test` database: the
evidence builder's SQL must run on the real schema, and its output must work
with the decision loop. Skipped unless AGENT_TEST_DATABASE_URL is set."""

import json
import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from use_cases.persona_decision_loop import ALLOWED_ACTIONS, build_evidence, decide, evidence_paths  # noqa: E402
from use_cases.pg_runner import make_pg_runner  # noqa: E402

URL = os.environ.get("AGENT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="AGENT_TEST_DATABASE_URL not set")

DEMO_TENANT = "11111111-1111-1111-1111-111111111111"
ROLE = "agent_readonly_persona_test"


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
def profile_with_transactions(runner):
    rows = runner("SELECT master_profile_id::text AS id FROM customer360.crm_transactions LIMIT 1")
    assert rows, "seeded database has no transactions"
    return rows[0]["id"]


def test_evidence_builder_runs_on_the_real_schema(runner, profile_with_transactions):
    evidence = build_evidence(runner, profile_with_transactions)

    assert evidence["persona"]["lifecycle_stage"] is not None or evidence["persona"]["risk_level"] is not None
    assert evidence["transactions"], "expected recent transactions for this profile"
    assert len(evidence["transactions"]) <= 10
    assert isinstance(evidence["features"], dict) and evidence["features"]


def test_evidence_contains_no_direct_identifiers(runner, profile_with_transactions):
    blob = json.dumps(build_evidence(runner, profile_with_transactions), default=str).lower()

    for key in ("email", "phone", "full_name", "first_name", "last_name", "national_id"):
        assert f'"{key}"' not in blob


def test_unknown_profile_has_no_evidence(runner):
    assert build_evidence(runner, str(uuid.uuid4())) == {}


def test_real_evidence_flows_through_the_decision_loop(runner, profile_with_transactions):
    evidence = build_evidence(runner, profile_with_transactions)
    cite = sorted(evidence_paths(evidence))[0]

    class _Provider:
        def complete(self, prompt):
            return json.dumps({
                "perception": ["Evidence loaded for this customer"],
                "interpretation": "Context taken from the seeded persona.",
                "action": {"code": ALLOWED_ACTIONS[0], "channel": "email", "rationale": "Default cadence."},
                "cited": [cite],
            })

    decision = decide(evidence, _Provider())

    assert decision.needs_review is False
    assert decision.action == ALLOWED_ACTIONS[0]
