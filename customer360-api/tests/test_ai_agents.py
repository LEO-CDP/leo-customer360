"""Unit tests for the independent ``/ai-agents`` router."""

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import get_db
from core.routers.ai_agent_api import ai_agent_router
from leo_customer360_dao.models.identity import CdpAiAgent


class AiAgentRouterTests(unittest.TestCase):
	def setUp(self):
		self.app = FastAPI()
		self.app.include_router(ai_agent_router)
		self.db = MagicMock()
		self.app.dependency_overrides[get_db] = lambda: self.db
		self.client = TestClient(self.app)

	@staticmethod
	def _agent(agent_code="churn_prediction_v2", **overrides):
		values = {
			"agent_code": agent_code,
			"display_name": "Churn Predictor",
			"model_type": "classification",
			"status": "ACTIVE",
			"prompt_engine": "none",
			"instruction_version": 1,
			"instruction_updated_by": "system",
			"instruction_note": "",
			"prompt_versions": [],
		}
		values.update(overrides)
		return CdpAiAgent(**values)

	def test_list_returns_agents_in_repository_order(self):
		self.db.execute.return_value.scalars.return_value.all.return_value = [self._agent()]

		response = self.client.get("/ai-agents")

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()[0]["agent_code"], "churn_prediction_v2")
		self.assertIn("ORDER BY", str(self.db.execute.call_args.args[0]))

	def test_list_filter_and_pagination_matrix(self):
		self.db.execute.return_value.scalars.return_value.all.return_value = []
		for params in ({"status": "ACTIVE"}, {"model_type": "generative_llm"}, {"skip": 5, "limit": 1}):
			with self.subTest(params=params):
				self.assertEqual(self.client.get("/ai-agents", params=params).status_code, 200)
		self.assertEqual(self.client.get("/ai-agents?limit=0").status_code, 200)
		self.assertEqual(self.client.get("/ai-agents?limit=999999").status_code, 422)

	def test_get_returns_agent_or_not_found(self):
		self.db.get.side_effect = lambda _, code: self._agent("clv_regression_v1") if code == "clv_regression_v1" else None
		self.assertEqual(self.client.get("/ai-agents/clv_regression_v1").status_code, 200)
		self.assertEqual(self.client.get("/ai-agents/missing").status_code, 404)

	def test_create_records_initial_prompt_revision(self):
		self.db.get.return_value = None
		response = self.client.post("/ai-agents", json={
			"agent_code": "profile_summary", "display_name": "Profile Summary",
			"model_type": "generative_llm", "system_instructions": "Summarize the profile.",
			"required_variables": ["profile"],
		})
		self.assertEqual(response.status_code, 201)
		created = self.db.add.call_args.args[0]
		self.assertEqual(created.instruction_version, 1)
		self.assertEqual(created.prompt_versions[0]["required_vars"], ["profile"])

	def test_create_rejection_matrix(self):
		self.db.get.return_value = None
		invalid_payloads = [
			{"display_name": "Missing code", "model_type": "classification"},
			{"agent_code": "no_body", "display_name": "No Body", "model_type": "generative_llm", "prompt_key": "key"},
		]
		for payload in invalid_payloads:
			with self.subTest(payload=payload):
				self.assertIn(self.client.post("/ai-agents", json=payload).status_code, {400, 422})

	def test_create_returns_conflict_for_existing_agent_code(self):
		self.db.get.return_value = self._agent("duplicate")
		response = self.client.post("/ai-agents", json={"agent_code": "duplicate", "display_name": "Duplicate", "model_type": "classification"})
		self.assertEqual(response.status_code, 400)

	def test_update_instruction_creates_revision_and_delete_handles_missing(self):
		agent = self._agent(system_instructions="Old", prompt_versions=[{"version": 1, "body": "Old", "required_vars": []}])
		self.db.get.return_value = agent
		self.db.refresh.side_effect = lambda value: setattr(value, "updated_at", datetime.now(timezone.utc))
		response = self.client.patch("/ai-agents/churn_prediction_v2", json={"system_instructions": "New", "instruction_updated_by": "reviewer"})
		self.assertEqual(response.status_code, 200)
		self.assertEqual(agent.instruction_version, 2)
		self.assertEqual(agent.prompt_versions[-1]["created_by"], "reviewer")
		self.assertEqual(self.client.delete("/ai-agents/churn_prediction_v2").status_code, 204)
		self.db.get.return_value = None
		self.assertEqual(self.client.patch("/ai-agents/missing", json={"display_name": "Missing"}).status_code, 404)
		self.assertEqual(self.client.delete("/ai-agents/missing").status_code, 404)