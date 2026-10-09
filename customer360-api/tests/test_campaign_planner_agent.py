"""Unit tests for core.repositories.campaign_planner_agent.resolve_planner.

The cdp_ai_agents repository is patched with a SimpleNamespace row, so no
database is needed.
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from core.repositories.campaign_planner_agent import AgentConfigurationInvalidError, resolve_planner
from core.repositories.metadata_repository import MetadataNotFoundError

AGENT_CODE = "campaign_planner_uat"
VARIABLES = ["target_segment", "objective", "budget", "time_constraints", "candidate_content_item_ids"]
BODY = "You are a campaign strategist. Return ONLY JSON."
SNAPSHOT_FIELDS = [
    "agent_code", "display_name", "description", "model_type", "model_name", "status", "schedule_definition",
    "input_features", "hyperparameters", "prompt_key", "prompt_engine", "system_instructions", "required_variables",
    "instruction_version", "instruction_updated_by", "instruction_note", "prompt_versions", "created_at", "updated_at",
]


def _context(**overrides):
    context = {
        "target_segment": {"segment_id": "s1"},
        "objective": "Win back lapsed customers",
        "budget": "5000000",
        "time_constraints": "Launch within 2 weeks",
        "candidate_content_item_ids": ["c1"],
    }
    context.update(overrides)
    return context


def _agent(**overrides):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    defaults = dict(
        agent_code=AGENT_CODE,
        display_name="Campaign Planning Agent (UAT)",
        description="Plans campaigns",
        model_type="generative_llm",
        model_name="openai/gpt-5.6-luna",
        status="ACTIVE",
        schedule_definition=None,
        input_features=list(VARIABLES),
        hyperparameters={"temperature": 0.2, "max_tokens": 1200, "response_format": {"type": "json_object"}},
        prompt_key="campaign.plan.uat.instructions",
        prompt_engine="none",
        system_instructions=BODY,
        required_variables=list(VARIABLES),
        instruction_version=1,
        instruction_updated_by="uat",
        instruction_note="seed",
        prompt_versions=[{"version": 1, "body": BODY, "required_vars": list(VARIABLES), "created_by": "uat"}],
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class ResolvePlannerTests(unittest.TestCase):
    def _resolve(self, agent=None, context=None, error=None):
        with patch("core.repositories.campaign_planner_agent.AiAgentRepository") as repo_cls:
            if error is not None:
                repo_cls.return_value.get_ai_agent.side_effect = error
            else:
                repo_cls.return_value.get_ai_agent.return_value = agent or _agent()
            return resolve_planner(None, AGENT_CODE, context or _context())

    def _assert_refused(self, fragment, **kwargs):
        with self.assertRaises(AgentConfigurationInvalidError) as ctx:
            self._resolve(**kwargs)
        self.assertEqual(ctx.exception.agent_code, AGENT_CODE)
        self.assertTrue(any(fragment in reason for reason in ctx.exception.reasons), ctx.exception.reasons)

    def test_valid_agent_resolves(self):
        agent = _agent()
        planner = self._resolve(agent)

        self.assertEqual(planner.model, "openrouter/openai/gpt-5.6-luna")
        self.assertEqual(planner.instructions, agent.system_instructions)
        self.assertEqual(planner.extra_config, agent.hyperparameters)

    def test_snapshot_has_every_agent_field_and_resolution_data(self):
        agent = _agent()
        snapshot = self._resolve(agent).snapshot

        for field in SNAPSHOT_FIELDS:
            self.assertIn(field, snapshot)
        self.assertEqual(snapshot["agent_code"], AGENT_CODE)
        self.assertEqual(snapshot["resolved_prompt_version"], agent.prompt_versions[0])
        self.assertIn("run_at", snapshot)

    def test_inactive_agent_refused(self):
        self._assert_refused("status", agent=_agent(status="INACTIVE"))

    def test_non_generative_model_type_refused(self):
        self._assert_refused("model_type", agent=_agent(model_type="classification"))

    def test_unsupported_model_name_refused(self):
        self._assert_refused("model_name", agent=_agent(model_name="gpt-6-luna"))

    def test_missing_prompt_version_entry_refused(self):
        self._assert_refused("prompt_versions", agent=_agent(instruction_version=2))

    def test_prompt_body_mismatch_refused(self):
        self._assert_refused("do not match", agent=_agent(system_instructions="edited outside the version log"))

    def test_missing_prompt_key_refused(self):
        self._assert_refused("prompt_key", agent=_agent(prompt_key=None))

    def test_unsupported_prompt_engine_refused(self):
        self._assert_refused("prompt_engine", agent=_agent(prompt_engine="jinja"))

    def test_missing_required_variable_refused(self):
        self._assert_refused("budget", context=_context(budget=None))

    def test_empty_list_counts_as_missing(self):
        self._assert_refused("candidate_content_item_ids", context=_context(candidate_content_item_ids=[]))

    def test_unknown_hyperparameter_refused(self):
        self._assert_refused("frequency_penalty", agent=_agent(hyperparameters={"frequency_penalty": 1}))

    def test_wrong_type_hyperparameter_refused(self):
        self._assert_refused("max_tokens", agent=_agent(hyperparameters={"max_tokens": "1200"}))

    def test_bool_hyperparameter_refused(self):
        self._assert_refused("temperature", agent=_agent(hyperparameters={"temperature": True}))

    def test_unregistered_agent_refused(self):
        self._assert_refused("not registered", error=MetadataNotFoundError("missing"))

    def test_registry_max_output_tokens_is_sent_as_max_tokens(self):
        agent = _agent(hyperparameters={"temperature": 0.2, "max_output_tokens": 1200})

        planner = self._resolve(agent)

        self.assertEqual(planner.extra_config, {"temperature": 0.2, "max_tokens": 1200})
        self.assertEqual(planner.snapshot["hyperparameters"], {"temperature": 0.2, "max_output_tokens": 1200})


if __name__ == "__main__":
    unittest.main()
