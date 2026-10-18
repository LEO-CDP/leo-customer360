"""Unit tests for WorkflowRepository governance logic, using a scripted fake session.

No database is needed. The SQL behaviour itself is covered by test_workflow_repository.py.
"""

import unittest
import uuid
from types import SimpleNamespace

from sqlalchemy.exc import DBAPIError

from leo_customer360_dao.workflow import FlowNotFound, WorkflowRuleViolation
from leo_customer360_dao.workflow.repository import WorkflowRepository

TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
FLOW_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
USER_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
WORKFLOW = {"schema_version": 2, "root": {"id": "n1", "type": "trigger", "config": {"event": "signup"}}}


class _Result:
    def __init__(self, row=None):
        self._row = row

    def one(self):
        if self._row is None:
            raise AssertionError("expected a row")
        return self._row

    def first(self):
        return self._row

    def mappings(self):
        return self

    def __getattr__(self, name):
        return getattr(self._row, name)


class FakeSession:
    """Records every statement and answers with scripted results or raised database errors."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if "set_config" in sql:
            return _Result(None)
        outcome = self.outcomes.pop(0) if self.outcomes else _Result(None)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def statements_containing(self, fragment):
        return [params for sql, params in self.calls if fragment in sql]


def _database_error(message: str, sqlstate: str = "P0001") -> DBAPIError:
    orig = SimpleNamespace(pgcode=sqlstate, diag=SimpleNamespace(message_primary=message))
    return DBAPIError("SELECT 1", {}, orig)


def _saved(flow_id=FLOW_ID, version=2):
    return _Result(SimpleNamespace(flow_id=flow_id, version=version))


class SaveTests(unittest.TestCase):
    def test_user_save_passes_author_and_no_agent(self):
        session = FakeSession(_saved())
        saved = WorkflowRepository(session, TENANT_ID).save("Welcome", WORKFLOW, user_id=USER_ID)

        params = session.statements_containing("crm_save_flow")[0]
        self.assertEqual(params["actor"], "user")
        self.assertEqual(params["user"], str(USER_ID))
        self.assertIsNone(params["agent"])
        self.assertEqual(params["provenance"], "{}")
        self.assertEqual((saved.flow_id, saved.version), (FLOW_ID, 2))

    def test_agent_save_passes_agent_code_and_provenance(self):
        session = FakeSession(_saved())
        WorkflowRepository(session, TENANT_ID).save(
            "Agent plan",
            WORKFLOW,
            actor="agent",
            user_id=USER_ID,
            agent_code=" campaign_planner ",
            provenance={"model": "m1", "prompt_version": "7"},
        )

        params = session.statements_containing("crm_save_flow")[0]
        self.assertEqual(params["actor"], "agent")
        self.assertEqual(params["agent"], "campaign_planner")
        self.assertEqual(params["user"], str(USER_ID))
        self.assertIn('"prompt_version": "7"', params["provenance"])

    def test_agent_save_without_agent_code_is_rejected_before_any_write(self):
        session = FakeSession()
        with self.assertRaises(ValueError):
            WorkflowRepository(session, TENANT_ID).save("Plan", WORKFLOW, actor="agent", agent_code="  ")
        self.assertEqual(session.calls, [])

    def test_agent_save_without_initiating_user_is_rejected_before_any_write(self):
        session = FakeSession()
        with self.assertRaises(ValueError):
            WorkflowRepository(session, TENANT_ID).save(
                "Plan",
                WORKFLOW,
                actor="agent",
                agent_code="campaign_planner",
            )
        self.assertEqual(session.calls, [])

    def test_user_save_cannot_claim_an_agent(self):
        session = FakeSession()
        with self.assertRaises(ValueError):
            WorkflowRepository(session, TENANT_ID).save("Plan", WORKFLOW, agent_code="lead_scoring")
        self.assertEqual(session.calls, [])

    def test_unknown_actor_and_non_object_provenance_are_rejected(self):
        repository = WorkflowRepository(FakeSession(), TENANT_ID)
        with self.assertRaises(ValueError):
            repository.save("Plan", WORKFLOW, actor="robot")
        with self.assertRaises(ValueError):
            repository.save("Plan", WORKFLOW, provenance=["not", "an", "object"])

    def test_blank_name_is_rejected(self):
        with self.assertRaises(ValueError):
            WorkflowRepository(FakeSession(), TENANT_ID).save("   ", WORKFLOW)

    def test_flow_with_config_problems_is_refused_before_any_sql(self):
        session = FakeSession()
        invalid = {
            "schema_version": 2,
            "root": {"id": "n1", "type": "trigger", "config": {"event": "not_an_event"}, "children": []},
        }
        with self.assertRaisesRegex(ValueError, "event must be one of"):
            WorkflowRepository(session, TENANT_ID).save("Plan", invalid)
        self.assertEqual(session.calls, [])

    def test_pending_approval_violation_becomes_rule_violation(self):
        session = FakeSession(_database_error("version 1 of flow x is InReview and must be approved"))
        with self.assertRaises(WorkflowRuleViolation):
            WorkflowRepository(session, TENANT_ID).save("Plan", WORKFLOW, flow_id=FLOW_ID)

    def test_missing_flow_for_this_tenant_becomes_flow_not_found(self):
        session = FakeSession(_database_error("flow abc not found for this tenant"))
        with self.assertRaises(FlowNotFound):
            WorkflowRepository(session, TENANT_ID).save("Plan", WORKFLOW, flow_id=FLOW_ID)

    def test_unrelated_database_error_is_re_raised_unchanged(self):
        error = _database_error("deadlock detected", sqlstate="40P01")
        session = FakeSession(error)
        with self.assertRaises(DBAPIError) as raised:
            WorkflowRepository(session, TENANT_ID).save("Plan", WORKFLOW)
        self.assertIs(raised.exception, error)


class PublishTests(unittest.TestCase):
    def test_unapproved_version_becomes_rule_violation(self):
        session = FakeSession(_database_error("version 2 of flow x is InReview and must be approved"))
        with self.assertRaises(WorkflowRuleViolation):
            WorkflowRepository(session, TENANT_ID).publish(FLOW_ID, 2)

    def test_archived_flow_becomes_rule_violation(self):
        session = FakeSession(_database_error("flow x is archived and cannot be published"))
        with self.assertRaises(WorkflowRuleViolation):
            WorkflowRepository(session, TENANT_ID).publish(FLOW_ID, 2)

    def test_no_row_means_not_found(self):
        session = FakeSession(_Result(None))
        with self.assertRaises(FlowNotFound):
            WorkflowRepository(session, TENANT_ID).publish(FLOW_ID, 9)

    def test_success_returns_previous_and_new_live_version(self):
        session = FakeSession(_Result(SimpleNamespace(previous_version=1, live_version=2)))
        self.assertEqual(WorkflowRepository(session, TENANT_ID).publish(FLOW_ID, 2), (1, 2))


class ReviewTests(unittest.TestCase):
    def test_approve_returns_new_status_and_binds_reviewer(self):
        session = FakeSession(_Result(SimpleNamespace(reviewed_status="Approved")))
        status = WorkflowRepository(session, TENANT_ID).review(FLOW_ID, 2, USER_ID, "approve", "ok")

        params = session.statements_containing("crm_review_flow_version")[0]
        self.assertEqual(status, "Approved")
        self.assertEqual(params["reviewer"], str(USER_ID))
        self.assertEqual(params["decision"], "approve")
        self.assertEqual(params["reason"], "ok")

    def test_invalid_decision_is_rejected_before_any_write(self):
        session = FakeSession()
        with self.assertRaises(ValueError):
            WorkflowRepository(session, TENANT_ID).review(FLOW_ID, 2, USER_ID, "publish")
        self.assertEqual(session.calls, [])

    def test_missing_reviewer_is_rejected_before_any_write(self):
        session = FakeSession()
        with self.assertRaises(ValueError):
            WorkflowRepository(session, TENANT_ID).review(FLOW_ID, 2, None, "approve")
        self.assertEqual(session.calls, [])

    def test_four_eyes_violation_becomes_rule_violation(self):
        session = FakeSession(_database_error("the person who started a run cannot approve its output"))
        with self.assertRaises(WorkflowRuleViolation):
            WorkflowRepository(session, TENANT_ID).review(FLOW_ID, 2, USER_ID, "approve")

    def test_unknown_version_becomes_flow_not_found(self):
        session = FakeSession(_database_error("version 9 of flow x not found for this tenant"))
        with self.assertRaises(FlowNotFound):
            WorkflowRepository(session, TENANT_ID).review(FLOW_ID, 9, USER_ID, "approve")


class VersionInfoTests(unittest.TestCase):
    def test_missing_version_is_flow_not_found(self):
        session = FakeSession(_Result(None))
        with self.assertRaises(FlowNotFound):
            WorkflowRepository(session, TENANT_ID).version_info(FLOW_ID, 3)

    def test_returns_governance_columns_only(self):
        row = {"version": 3, "created_by": "agent", "agent_code": "lead_scoring", "approval_status": "InReview"}
        session = FakeSession(_Result(row))
        info = WorkflowRepository(session, TENANT_ID).version_info(FLOW_ID, 3)

        sql = session.calls[-1][0]
        self.assertNotIn("SELECT *", sql)
        self.assertNotIn("workflow_json", sql)
        self.assertEqual(info["approval_status"], "InReview")


if __name__ == "__main__":
    unittest.main()
