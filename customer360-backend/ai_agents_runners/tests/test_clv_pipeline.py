from uuid import UUID

import pytest

import ai_agents_runners.agent_pipeline.pipelines as pipelines
from ai_agents_runners.agent_pipeline.agent_types.regression import RegressionPipeline
from ai_agents_runners.agent_pipeline.pipelines import (
    execute_agent_pipeline,
    execute_agent_pipeline_batch,
)
from ai_agents_runners.clv.service import ScoredProfile
from ai_agents_runners.runner import AgentWorkflowMasterTask, WorkflowRunSummary

TENANT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT_ID = "33333333-3333-3333-3333-333333333333"
SEGMENT_ID = "22222222-2222-2222-2222-222222222222"
PROFILE_A = "aaaaaaaa-0000-0000-0000-000000000001"
PROFILE_B = "aaaaaaaa-0000-0000-0000-000000000002"
VALUATION_ID = "bbbbbbbb-0000-0000-0000-000000000001"


class FakeScorer:
    def __init__(self):
        self.calls = []

    def score_profiles(self, tenant_id, profile_ids, configuration=None):
        self.calls.append((tenant_id, list(profile_ids), configuration))
        return {
            profile_id: (
                ScoredProfile(
                    profile_id, "COMPUTED", value=1234.5, valuation_id=VALUATION_ID,
                    clv_segment="high_value", clv_percentile=88.0,
                )
                if profile_id == PROFILE_A
                else ScoredProfile(profile_id, "NO_CHAMPION")
            )
            for profile_id in profile_ids
        }


def _payload(tenant_id=TENANT_ID, profile_id=PROFILE_A, agent_code="clv_prediction"):
    return {
        "tenant_id": tenant_id,
        "segment_id": SEGMENT_ID,
        "agent_code": agent_code,
        "model_type": "regression",
        "input_data": {"master_profile_id": profile_id},
    }


@pytest.fixture
def scorer(monkeypatch):
    fake = FakeScorer()
    monkeypatch.setitem(
        pipelines.PIPELINE_HANDLERS, "regression", RegressionPipeline(lambda: fake)
    )
    return fake


def test_regression_pipeline_returns_projected_clv(scorer):
    output = execute_agent_pipeline(_payload(), run_id="run-1")

    assert output.result == {
        "value": 1234.5,
        "valuation_id": VALUATION_ID,
        "clv_segment": "high_value",
        "clv_percentile": 88.0,
        "calculation_status": "COMPUTED",
    }
    assert scorer.calls == [(TENANT_ID, [PROFILE_A], {})]


def test_regression_pipeline_reports_missing_champion_without_a_value(scorer):
    output = execute_agent_pipeline(_payload(profile_id=PROFILE_B), run_id="run-1")

    assert output.result["value"] is None
    assert output.result["calculation_status"] == "NO_CHAMPION"


def test_regression_batch_scores_once_per_tenant(scorer):
    outputs = execute_agent_pipeline_batch(
        [
            _payload(profile_id=PROFILE_A),
            _payload(tenant_id=OTHER_TENANT_ID, profile_id=PROFILE_B),
            _payload(profile_id=PROFILE_B),
        ],
        run_id="run-1",
    )

    assert [output.tenant_id for output in outputs] == [
        UUID(TENANT_ID), UUID(OTHER_TENANT_ID), UUID(TENANT_ID)
    ]
    assert [call[:2] for call in scorer.calls] == [
        (TENANT_ID, [PROFILE_A, PROFILE_B]),
        (OTHER_TENANT_ID, [PROFILE_B]),
    ]


def test_regression_pipeline_validates_the_profile_id(scorer):
    with pytest.raises(ValueError, match="master_profile_id must be a UUID"):
        execute_agent_pipeline(_payload(profile_id="not-a-uuid"), run_id="run-1")


def test_regression_pipeline_only_handles_the_clv_agent(scorer):
    with pytest.raises(NotImplementedError, match="regression"):
        execute_agent_pipeline(_payload(agent_code="other_regression"), run_id="run-1")
    assert scorer.calls == []


def test_workflow_runner_rescores_clv_segment_members(monkeypatch):
    fake = FakeScorer()
    task = AgentWorkflowMasterTask(
        connection_factory=lambda: pytest.fail("no direct DB access expected"),
        clv_scorer_factory=lambda: fake,
    )
    monkeypatch.setattr(
        task,
        "_load_segment_profiles",
        lambda *, tenant_id, segment_id: [
            {"master_profile_id": UUID(PROFILE_A)},
            {"master_profile_id": UUID(PROFILE_B)},
        ],
    )
    step = {
        "tenant_id": TENANT_ID,
        "segment_id": SEGMENT_ID,
        "agent_code": "clv_prediction",
        "model_type": "regression",
        "execution_order": 1,
        "configuration": {"min_population": 500},
        "candidate_content_item_ids": [],
        "trigger_event": {},
    }
    summary = WorkflowRunSummary(
        trigger="api", tenant_id=TENANT_ID, segment_id=SEGMENT_ID,
        selected_steps=1, skipped_steps=0, plan=(step,),
    )

    result = task.execute(summary, run_id="run-1")

    assert result.clv_profiles_scored == 1
    assert result.executed_steps == 1
    assert result.unsupported_steps == 0
    assert result.as_dict()["clv_profiles_scored"] == 1
    assert fake.calls == [(TENANT_ID, [PROFILE_A, PROFILE_B], {"min_population": 500})]
