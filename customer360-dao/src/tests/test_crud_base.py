import unittest
import uuid
from unittest.mock import MagicMock

from leo_customer360_dao.crud.base import CRUDBase
from leo_customer360_dao.models.identity import CdpAiAgent, CdpDomainProfile


class TestCRUDBase(unittest.TestCase):
    def test_list_applies_desc_sort_to_mapped_column(self):
        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.all.return_value = []

        crud = CRUDBase(CdpAiAgent)
        crud.list(mock_db, sort_by="updated_at DESC")

        executed_stmt = mock_db.execute.call_args.args[0]
        rendered_sql = str(executed_stmt)
        self.assertIn("ORDER BY", rendered_sql)
        self.assertIn("cdp_ai_agents.updated_at DESC", rendered_sql)

    def test_list_rejects_invalid_sort_direction(self):
        mock_db = MagicMock()
        crud = CRUDBase(CdpAiAgent)

        with self.assertRaises(ValueError):
            crud.list(mock_db, sort_by="updated_at DESC; DROP TABLE cdp_ai_agents")

        mock_db.execute.assert_not_called()

    def test_update_sets_timezone_aware_updated_at_from_db_clock(self):
        mock_db = MagicMock()
        crud = CRUDBase(CdpAiAgent)
        model = CdpAiAgent(
            agent_code="churn_prediction_v2",
            display_name="XGBoost Churn Predictor",
            model_type="classification",
            status="ACTIVE",
        )

        crud.update(mock_db, model, {"display_name": "Updated Model"})

        self.assertEqual(model.display_name, "Updated Model")
        self.assertEqual(str(model.updated_at), "now()")

    def test_update_sets_naive_updated_at_in_utc_from_db_clock(self):
        mock_db = MagicMock()
        crud = CRUDBase(CdpDomainProfile)
        model = CdpDomainProfile(
            tenant_id=uuid.uuid4(),
            master_profile_id=uuid.uuid4(),
            domain_id=uuid.uuid4(),
        )

        crud.update(mock_db, model, {"profile_name": "VIP Shopper"})

        self.assertEqual(model.profile_name, "VIP Shopper")
        self.assertIn("timezone(:timezone_1, now())", str(model.updated_at))

    def test_ai_agent_model_defaults_and_fields(self):
        agent = CdpAiAgent(
            agent_code="test_agent",
            display_name="Test AI Agent",
            model_type="generative_llm",
            model_name="gpt-5.6",
            status="ACTIVE",
            prompt_key="test.prompt.key",
            system_instructions="Test prompt instructions",
            required_variables=["var1", "var2"],
            instruction_version=1,
            instruction_updated_by="system",
            instruction_note="initial",
            prompt_versions=[{"version": 1, "body": "Test prompt instructions"}],
        )
        self.assertEqual(agent.__tablename__, "cdp_ai_agents")
        self.assertEqual(agent.agent_code, "test_agent")
        self.assertEqual(agent.model_type, "generative_llm")
        self.assertEqual(agent.model_name, "gpt-5.6")
        self.assertEqual(agent.prompt_key, "test.prompt.key")
        self.assertEqual(agent.instruction_version, 1)
        self.assertEqual(len(agent.prompt_versions), 1)
        self.assertEqual(agent.prompt_versions[0]["body"], "Test prompt instructions")


if __name__ == "__main__":
    unittest.main()