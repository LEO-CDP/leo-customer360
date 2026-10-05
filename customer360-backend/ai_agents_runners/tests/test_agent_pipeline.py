from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from ai_agents_runners.agent_pipeline.contracts import AgentPipelineInput, AgentPipelineOutput
from ai_agents_runners.agent_pipeline.agent_types.classification import ClassificationPipeline
from ai_agents_runners.agent_pipeline.pipelines import (
    PIPELINE_HANDLERS,
    execute_agent_pipeline,
)
import ai_agents_runners.agent_pipeline.pipelines as pipelines

TENANT_ID = "11111111-1111-1111-1111-111111111111"
SEGMENT_ID = "22222222-2222-2222-2222-222222222222"


def _input(model_type="classification", **overrides):
    payload = {
        "tenant_id": TENANT_ID,
        "segment_id": SEGMENT_ID,
        "agent_code": "test_agent",
        "model_type": model_type,
        "input_data": {"features": {"visits": 3}},
    }
    payload.update(overrides)
    return payload


def test_pipeline_input_validates_tenant_scope_and_agent_code():
    payload = AgentPipelineInput.model_validate(_input())

    assert payload.tenant_id == UUID(TENANT_ID)
    assert payload.agent_code == "test_agent"


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"tenant_id": "not-a-uuid"}, "tenant_id"),
        ({"agent_code": " "}, "agent_code"),
        ({"model_type": "lead_scoring"}, "model_type"),
        ({"candidate_content_item_ids": [SEGMENT_ID]}, "only valid"),
        (
            {
                "model_type": "ranking_recommendation",
                "candidate_content_item_ids": [SEGMENT_ID, SEGMENT_ID],
            },
            "unique",
        ),
        ({"input_data": {"score": float("nan")}}, "must be finite"),
    ],
)
def test_pipeline_input_rejects_invalid_payload(overrides, message):
    with pytest.raises(ValidationError, match=message):
        AgentPipelineInput.model_validate(_input(**overrides))


@pytest.mark.parametrize(
    "model_type",
    [model_type for model_type in PIPELINE_HANDLERS if model_type != "ranking_recommendation"],
)
def test_each_agent_type_has_an_explicit_unimplemented_handler(model_type):
    with pytest.raises(NotImplementedError, match=model_type):
        execute_agent_pipeline(_input(model_type), run_id="dagster-run-1")


def test_pipeline_dispatch_returns_validated_tenant_scoped_output(monkeypatch):
    handler = ClassificationPipeline()
    monkeypatch.setattr(
        handler,
        "process",
        lambda _payload: {"label": "high_value", "probability": 0.9},
    )
    monkeypatch.setitem(pipelines.PIPELINE_HANDLERS, "classification", handler)

    output = execute_agent_pipeline(_input(), run_id="dagster-run-1")

    assert output.tenant_id == UUID(TENANT_ID)
    assert output.segment_id == UUID(SEGMENT_ID)
    assert output.run_id == "dagster-run-1"
    assert output.result["label"] == "high_value"


def test_pipeline_dispatch_validates_handler_output(monkeypatch):
    handler = ClassificationPipeline()
    monkeypatch.setattr(handler, "process", lambda _payload: {"probability": 0.9})
    monkeypatch.setitem(pipelines.PIPELINE_HANDLERS, "classification", handler)

    with pytest.raises(ValidationError, match="label"):
        execute_agent_pipeline(_input(), run_id="dagster-run-1")


@pytest.mark.parametrize(
    "model_type, result",
    [
        ("classification", {"label": "high_value"}),
        ("regression", {"value": 12.5}),
        ("clustering", {"cluster_id": "cluster-a"}),
        ("ranking_recommendation", {"ranked_items": []}),
        ("forecasting", {"forecast_series": []}),
        ("anomaly_detection", {"anomaly_score": 0.9, "is_anomaly": True}),
        ("uplift_modeling", {"uplift_score": 0.2}),
        ("semantic_embedding", {"embedding": [0.1, 0.2]}),
        ("graph_ml", {"relationship_signals": {"connected": True}}),
        ("optimization", {"selected_action": "send_offer"}),
        ("rules_engine", {"decision": "allow", "decision_trace": []}),
        ("generative_llm", {"text": "summary"}),
    ],
)
def test_pipeline_output_validates_each_agent_result_shape(model_type, result):
    result = PIPELINE_HANDLERS[model_type].result_model.model_validate(
        result
    ).model_dump(mode="json")
    output = AgentPipelineOutput.model_validate(
        {
            "run_id": "dagster-run-1",
            "tenant_id": TENANT_ID,
            "segment_id": SEGMENT_ID,
            "agent_code": "test_agent",
            "model_type": model_type,
            "result": result,
            "completed_at": datetime.now(timezone.utc),
        }
    )

    assert output.tenant_id == UUID(TENANT_ID)
    assert output.result == result


@pytest.mark.parametrize(
    "model_type, result",
    [
        ("classification", {"label": "yes", "probability": 1.1}),
        ("regression", {"value": float("inf")}),
        ("ranking_recommendation", {"ranked_items": {}}),
        ("anomaly_detection", {"anomaly_score": 0.4, "is_anomaly": "no"}),
        ("semantic_embedding", {"embedding": [0.2, float("nan")]}),
        ("rules_engine", {"decision": "maybe", "decision_trace": []}),
    ],
)
def test_pipeline_output_rejects_invalid_agent_results(model_type, result):
    with pytest.raises(ValidationError):
        PIPELINE_HANDLERS[model_type].result_model.model_validate(result)


@pytest.mark.parametrize(
    "model_type, expected_result",
    [
        pytest.param(
            "classification",
            {"label": "high_value", "probability": 0.9},
            marks=pytest.mark.skip(reason="TODO: implement classification inference"),
            id="classification",
        ),
        pytest.param(
            "regression",
            {"value": 12.5},
            marks=pytest.mark.skip(reason="TODO: implement regression inference"),
            id="regression",
        ),
        pytest.param(
            "clustering",
            {"cluster_id": "cluster-a", "membership_score": 0.8},
            marks=pytest.mark.skip(reason="TODO: implement clustering inference"),
            id="clustering",
        ),
        pytest.param(
            "forecasting",
            {"forecast_series": [{"period": "2026-11", "value": 14.0}]},
            marks=pytest.mark.skip(reason="TODO: implement forecasting"),
            id="forecasting",
        ),
        pytest.param(
            "anomaly_detection",
            {"anomaly_score": 0.9, "is_anomaly": True},
            marks=pytest.mark.skip(reason="TODO: implement anomaly detection"),
            id="anomaly-detection",
        ),
        pytest.param(
            "uplift_modeling",
            {"uplift_score": 0.2},
            marks=pytest.mark.skip(reason="TODO: implement uplift modeling"),
            id="uplift-modeling",
        ),
        pytest.param(
            "semantic_embedding",
            {"embedding": [0.1, 0.2, 0.3]},
            marks=pytest.mark.skip(reason="TODO: implement semantic embeddings"),
            id="semantic-embedding",
        ),
        pytest.param(
            "graph_ml",
            {"relationship_signals": {"connected": True}},
            marks=pytest.mark.skip(reason="TODO: implement graph intelligence"),
            id="graph-intelligence",
        ),
        pytest.param(
            "optimization",
            {"selected_action": "send_offer"},
            marks=pytest.mark.skip(reason="TODO: implement optimization and decisioning"),
            id="optimization",
        ),
        pytest.param(
            "rules_engine",
            {"decision": "allow", "decision_trace": [{"rule": "eligible", "matched": True}]},
            marks=pytest.mark.skip(reason="TODO: implement rules evaluation"),
            id="rules-engine",
        ),
        pytest.param(
            "generative_llm",
            {"text": "Customer summary"},
            marks=pytest.mark.skip(reason="TODO: implement generative LLM processing"),
            id="generative-llm",
        ),
    ],
)
def test_agent_type_pipeline_returns_expected_result(model_type, expected_result):
    """TODO: enable each case when its corresponding handler is implemented."""
    payload = _input(model_type)
    if model_type == "ranking_recommendation":
        payload["candidate_content_item_ids"] = [SEGMENT_ID]

    output = execute_agent_pipeline(payload, run_id="dagster-run-1")

    assert output.model_type == model_type
    assert output.tenant_id == UUID(TENANT_ID)
    assert output.segment_id == UUID(SEGMENT_ID)
    assert output.result == expected_result
