"""Graph-intelligence pipeline and result contract."""

from typing import Any, ClassVar

from ..contracts import AgentResultModel, ModelType
from .base import AgentTypePipeline


class GraphIntelligenceResult(AgentResultModel):
    relationship_signals: dict[str, Any]


class GraphIntelligencePipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "graph_ml"
    result_model = GraphIntelligenceResult
