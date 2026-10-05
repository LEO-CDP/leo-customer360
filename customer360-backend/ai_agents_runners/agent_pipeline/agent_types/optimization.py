"""Optimization pipeline and result contract."""

from typing import ClassVar

from pydantic import StrictStr

from ..contracts import AgentResultModel, ModelType
from .base import AgentTypePipeline


class OptimizationResult(AgentResultModel):
    selected_action: StrictStr


class OptimizationPipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "optimization"
    result_model = OptimizationResult
