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
from leo_customer360_dao.workflow import FlowEngine, FlowNotFound, WorkflowRepository

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
        with self.assertRaises(DBAPIError):
            other.save("Private", self.recipes["welcome"]["workflow"], flow_id=saved.flow_id)

    def test_rejects_invalid_workflow_before_writing(self):
        with self.assertRaises(ValueError):
            self.repository.save("Broken", {"schema_version": 2, "root": {"id": "n1"}})
        self.assertEqual(self.repository.list_flows(), [])

    def test_requires_a_name(self):
        with self.assertRaises(ValueError):
            self.repository.save("  ", self.recipes["welcome"]["workflow"])


if __name__ == "__main__":
    unittest.main()
