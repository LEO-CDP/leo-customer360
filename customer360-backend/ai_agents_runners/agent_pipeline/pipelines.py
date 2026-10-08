"""Shared execution boundary and model-type strategy registry."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from .agent_types import (
    AnomalyDetectionPipeline,
    ClassificationPipeline,
    ClusteringPipeline,
    ForecastingPipeline,
    GenerativeLlmPipeline,
    GraphIntelligencePipeline,
    OptimizationPipeline,
    RankingRecommendationPipeline,
    RegressionPipeline,
    RulesEnginePipeline,
    SemanticEmbeddingPipeline,
    UpliftModelingPipeline,
)
from .agent_types.base import AgentTypePipeline
from .contracts import AgentPipelineInput, AgentPipelineOutput, ModelType

PIPELINE_HANDLERS: dict[ModelType, AgentTypePipeline] = {
    "classification": ClassificationPipeline(),
    "regression": RegressionPipeline(),
    "clustering": ClusteringPipeline(),
    "ranking_recommendation": RankingRecommendationPipeline(),
    "forecasting": ForecastingPipeline(),
    "anomaly_detection": AnomalyDetectionPipeline(),
    "uplift_modeling": UpliftModelingPipeline(),
    "semantic_embedding": SemanticEmbeddingPipeline(),
    "graph_ml": GraphIntelligencePipeline(),
    "optimization": OptimizationPipeline(),
    "rules_engine": RulesEnginePipeline(),
    "generative_llm": GenerativeLlmPipeline(),
}


def execute_agent_pipeline(
    raw_payload: dict[str, Any],
    *,
    run_id: str,
) -> AgentPipelineOutput:
    """Validate input, dispatch its strategy, and build the output envelope."""
    payload = AgentPipelineInput.model_validate(raw_payload)
    handler = PIPELINE_HANDLERS[payload.model_type]
    result = handler(payload)
    return AgentPipelineOutput.model_validate(
        {
            "run_id": run_id,
            "tenant_id": payload.tenant_id,
            "segment_id": payload.segment_id,
            "agent_code": payload.agent_code,
            "model_type": payload.model_type,
            "result": result,
            "completed_at": datetime.now(timezone.utc),
        }
    )


def execute_agent_pipeline_batch(
    raw_payloads: Sequence[dict[str, Any]],
    *,
    run_id: str,
) -> list[AgentPipelineOutput]:
    """Validate and dispatch a homogeneous batch of pipeline inputs."""
    if not raw_payloads:
        return []

    first_raw_payload = raw_payloads[0]
    first_payload = AgentPipelineInput.model_validate(first_raw_payload)
    shared_candidate_ids = first_raw_payload.get("candidate_content_item_ids", [])
    shared_configuration = first_raw_payload.get("configuration", {})
    payloads = [first_payload]
    for raw_payload in raw_payloads[1:]:
        if raw_payload.get("candidate_content_item_ids", []) != shared_candidate_ids:
            raise ValueError(
                "Pipeline batches must share candidate_content_item_ids"
            )
        if raw_payload.get("configuration", {}) != shared_configuration:
            raise ValueError("Pipeline batches must share configuration")
        profile_payload = dict(raw_payload)
        profile_payload["candidate_content_item_ids"] = []
        profile_payload["configuration"] = {}
        payload = AgentPipelineInput.model_validate(profile_payload)
        payloads.append(
            payload.model_copy(
                update={
                    "candidate_content_item_ids": first_payload.candidate_content_item_ids,
                    "configuration": first_payload.configuration,
                }
            )
        )
    model_type = payloads[0].model_type
    if any(payload.model_type != model_type for payload in payloads):
        raise ValueError("A pipeline batch must contain one model_type")

    handler = PIPELINE_HANDLERS[model_type]
    results = handler.process_batch(payloads)
    if len(results) != len(payloads):
        raise RuntimeError(
            f"{handler.__class__.__name__} returned {len(results)} results "
            f"for {len(payloads)} inputs"
        )

    outputs = []
    for payload, raw_result in zip(payloads, results, strict=True):
        result = handler.result_model.model_validate(raw_result).model_dump(mode="json")
        outputs.append(
            AgentPipelineOutput.model_validate(
                {
                    "run_id": run_id,
                    "tenant_id": payload.tenant_id,
                    "segment_id": payload.segment_id,
                    "agent_code": payload.agent_code,
                    "model_type": payload.model_type,
                    "result": result,
                    "completed_at": datetime.now(timezone.utc),
                }
            )
        )
    return outputs
