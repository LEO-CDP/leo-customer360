"""Shared execution boundary and model-type strategy registry."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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
