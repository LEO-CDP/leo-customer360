"""Integration tests: the studio's workflow JSON saved to and loaded from PostgreSQL.

The recipes in fixtures/workflow_recipes.json are representative studio workflow
payloads using fields available on CdpMasterProfile.
They run in a transaction that is rolled back, and skip when PostgreSQL is unreachable.
Apply customer360-database/database-schema.sql first.
"""

import json
import unittest
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from leo_customer360_dao.config import settings
from leo_customer360_dao.workflow import FlowEngine, FlowNotFound, WorkflowRepository, WorkflowRuleViolation

FIXTURE = Path(__file__).parent / "fixtures" / "workflow_recipes.json"


def _connect_or_skip():
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        return engine, engine.connect()
    except OperationalError as error:
        engine.dispose()
        raise unittest.SkipTest(f"PostgreSQL not reachable: {error.orig}") from error


class WorkflowRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipes = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.engine, cls.connection = _connect_or_skip()

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()
        cls.engine.dispose()

    def setUp(self):
        self.transaction = self.connection.begin()
        self.session = Session(bind=self.connection)
        self.tenant_id = uuid.uuid4()
        self.other_tenant_id = uuid.uuid4()
        for tenant in (self.tenant_id, self.other_tenant_id):
            self.session.execute(
                text(
                    "INSERT INTO customer360.sys_tenant (tenant_id, tenant_code, tenant_name, company_name, business_type) "
                    "VALUES (:id, :code, 'Test', 'Test Co', 'Retail')"
                ),
                {"id": str(tenant), "code": f"wf-{tenant}"},
            )
        self.repository = WorkflowRepository(self.session, self.tenant_id)

    def tearDown(self):
        self.session.close()
        self.transaction.rollback()

    def test_every_workflow_fixture_round_trips_exactly(self):
        for key, recipe in self.recipes.items():
            with self.subTest(recipe=key):
                saved = self.repository.save(recipe["name"], recipe["workflow"])
                self.assertEqual(self.repository.load_workflow(saved.flow_id), recipe["workflow"])
                self.assertEqual(FlowEngine(self.repository.load_definition(saved.flow_id)).problems(), [])

    def test_saved_recipe_nodes_and_edges_are_queryable(self):
        recipe = self.recipes["engagement_score_follow_up"]
        saved = self.repository.save(recipe["name"], recipe["workflow"])
        nodes = self.session.execute(
            text(
                "SELECT count(*) AS total, count(channel) AS messages FROM customer360.crm_vw_flow_node "
                "WHERE flow_id = CAST(:flow AS uuid) AND version = :version"
            ),
            {"flow": str(saved.flow_id), "version": saved.version},
        ).one()
        self.assertEqual(nodes.total, 12)
        self.assertEqual(nodes.messages, 3)

    def test_notification_and_audience_sync_round_trip(self):
        workflow = {
            "schema_version": 2,
            "root": {
                "id": "n1", "type": "trigger", "config": {"event": "signup"},
                "children": [{
                    "id": "n2", "type": "notification",
                    "config": {"provider": "whatsapp", "body": "Hello {{first_name}}"},
                    "children": [{
                        "id": "n3", "type": "run_data_synch_job",
                        "config": {"platform": "meta_ads", "audience_name": "UAT"},
                        "children": [],
                    }],
                }],
            },
        }
        saved = self.repository.save("Notification and audience sync", workflow)
        self.assertEqual(self.repository.load_workflow(saved.flow_id, saved.version), workflow)
        self.assertEqual(FlowEngine(self.repository.load_definition(saved.flow_id)).problems(), [])

    def test_sql_rejects_invalid_actor_name_and_schema_version(self):
        for actor, name, schema_version in (
            (None, "UAT", 2),
            ("unknown", "UAT", 2),
            ("user", " ", 2),
            ("user", "UAT", 3),
        ):
            with self.subTest(actor=actor, name=name, schema_version=schema_version):
                with self.assertRaises(DBAPIError), self.session.begin_nested():
                    self.session.execute(
                        text(
                            "SELECT flow_id, version FROM customer360.crm_save_flow("
                            "CAST(:tenant AS uuid), NULL::uuid, :name, CAST(:workflow AS jsonb), "
                            "CAST(:actor AS text), NULL::uuid, NULL::varchar, '{}'::jsonb)"
                        ),
                        {
                            "tenant": str(self.tenant_id), "name": name, "actor": actor,
                            "workflow": json.dumps({
                                "schema_version": schema_version,
                                "root": {"id": "n1", "type": "trigger", "config": {"event": "signup"}},
                            }),
                        },
                    )

    def test_each_save_creates_a_new_version_and_old_versions_stay_readable(self):
        first = self.recipes["welcome"]["workflow"]
        second = self.recipes["winback"]["workflow"]
        saved = self.repository.save("Lifecycle", first)
        again = self.repository.save("Lifecycle", second, flow_id=saved.flow_id)
        self.assertEqual((saved.version, again.version), (1, 2))
        self.assertEqual(self.repository.load_workflow(saved.flow_id, version=1), first)
        self.assertEqual(self.repository.load_workflow(saved.flow_id), second)

    def test_publish_sets_the_live_version(self):
        saved = self.repository.save("Live test", self.recipes["welcome"]["workflow"])
        self.repository.save("Live test", self.recipes["winback"]["workflow"], flow_id=saved.flow_id)
        self.assertEqual(self.repository.publish(saved.flow_id, 1), (None, 1))
        self.assertEqual(self.repository.publish(saved.flow_id, 2), (1, 2))
        self.assertEqual(self.repository.load_workflow(saved.flow_id), self.recipes["winback"]["workflow"])
        self.assertEqual(self.repository.list_flows()[0]["live_version"], 2)

    def test_publish_unknown_version_fails(self):
        saved = self.repository.save("Missing", self.recipes["welcome"]["workflow"])
        with self.assertRaises(FlowNotFound):
            self.repository.publish(saved.flow_id, 99)

    def test_other_tenant_cannot_load_or_update_a_flow(self):
        saved = self.repository.save("Private", self.recipes["welcome"]["workflow"])
        other = WorkflowRepository(self.session, self.other_tenant_id)
        with self.assertRaises(FlowNotFound):
            other.load_workflow(saved.flow_id)
        with self.assertRaises(FlowNotFound):
            other.save("Private", self.recipes["welcome"]["workflow"], flow_id=saved.flow_id)

    def test_user_save_is_approved_on_arrival_and_records_its_author(self):
        user_id = self._seed_user(self.tenant_id)
        saved = self.repository.save("Manual", self.recipes["welcome"]["workflow"], user_id=user_id)
        info = self.repository.version_info(saved.flow_id, saved.version)
        self.assertEqual((info["approval_status"], info["created_by"]), ("Approved", "user"))
        self.assertEqual(info["reviewed_by"], user_id)
        self.assertIsNone(info["agent_code"])

    def test_agent_version_waits_for_a_human_before_it_can_go_live(self):
        agent = self._seed_agent()
        initiator = self._seed_user(self.tenant_id)
        saved = self.repository.save(
            "Agent plan",
            self.recipes["winback"]["workflow"],
            actor="agent",
            agent_code=agent,
            user_id=initiator,
            provenance={"model": "test-model", "prompt_version": "1"},
        )
        info = self.repository.version_info(saved.flow_id, saved.version)
        self.assertEqual((info["approval_status"], info["created_by"], info["agent_code"]), ("InReview", "agent", agent))
        self.assertIsNone(info["reviewed_by"])

        with self.assertRaises(WorkflowRuleViolation):
            with self.session.begin_nested():
                self.repository.publish(saved.flow_id, saved.version)

    def test_initiator_cannot_approve_their_own_agent_run(self):
        agent = self._seed_agent()
        initiator = self._seed_user(self.tenant_id)
        reviewer = self._seed_user(self.tenant_id)
        saved = self.repository.save(
            "Agent plan", self.recipes["winback"]["workflow"], actor="agent", agent_code=agent, user_id=initiator
        )

        with self.assertRaises(WorkflowRuleViolation):
            with self.session.begin_nested():
                self.repository.review(saved.flow_id, saved.version, initiator, "approve")

        status = self.repository.review(saved.flow_id, saved.version, reviewer, "approve", "checked")
        self.assertEqual(status, "Approved")
        self.assertEqual(self.repository.publish(saved.flow_id, saved.version), (None, saved.version))

    def test_rejected_agent_version_can_never_go_live(self):
        agent = self._seed_agent()
        initiator = self._seed_user(self.tenant_id)
        reviewer = self._seed_user(self.tenant_id)
        saved = self.repository.save(
            "Agent plan",
            self.recipes["winback"]["workflow"],
            actor="agent",
            agent_code=agent,
            user_id=initiator,
        )
        self.assertEqual(self.repository.review(saved.flow_id, saved.version, reviewer, "reject", "no"), "Rejected")

        with self.assertRaises(WorkflowRuleViolation):
            with self.session.begin_nested():
                self.repository.publish(saved.flow_id, saved.version)

    def test_reviewing_a_user_version_is_refused(self):
        saved = self.repository.save("Manual", self.recipes["welcome"]["workflow"])
        reviewer = self._seed_user(self.tenant_id)
        with self.assertRaises(WorkflowRuleViolation):
            with self.session.begin_nested():
                self.repository.review(saved.flow_id, saved.version, reviewer, "approve")

    def _seed_user(self, tenant_id):
        user_id = uuid.uuid4()
        self.session.execute(
            text("INSERT INTO customer360.sys_user (user_id, tenant_id, username) VALUES (:id, :tenant, :name)"),
            {"id": str(user_id), "tenant": str(tenant_id), "name": f"wf-{user_id}"},
        )
        return user_id

    def _seed_agent(self):
        agent_code = f"wf-test-{uuid.uuid4().hex[:12]}"
        self.session.execute(
            text(
                "INSERT INTO customer360.cdp_ai_agents (agent_code, display_name, model_type, system_instructions) "
                "VALUES (:code, 'Workflow test agent', 'generative_llm', 'test')"
            ),
            {"code": agent_code},
        )
        return agent_code

    def test_rejects_invalid_workflow_before_writing(self):
        with self.assertRaises(ValueError):
            self.repository.save("Broken", {"schema_version": 2, "root": {"id": "n1"}})
        self.assertEqual(self.repository.list_flows(), [])

    def test_requires_a_name(self):
        with self.assertRaises(ValueError):
            self.repository.save("  ", self.recipes["welcome"]["workflow"])


if __name__ == "__main__":
    unittest.main()
