"""Rules-engine pipeline and result contract."""

from typing import Any, ClassVar, Literal

from ..contracts import AgentResultModel, ModelType
from .base import AgentTypePipeline


class RulesEngineResult(AgentResultModel):
    decision: Literal["allow", "deny", "review"]
    decision_trace: list[Any]


class RulesEnginePipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "rules_engine"
    result_model = RulesEngineResult
