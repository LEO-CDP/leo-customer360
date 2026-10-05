"""Classification pipeline and result contract."""

from typing import ClassVar

from pydantic import Field, FiniteFloat, StrictInt, StrictStr

from ..contracts import AgentResultModel, ModelType
from .base import AgentTypePipeline


class ClassificationResult(AgentResultModel):
    label: StrictStr | StrictInt
    probability: FiniteFloat | None = Field(default=None, ge=0, le=1)
    confidence: FiniteFloat | None = Field(default=None, ge=0, le=1)


class ClassificationPipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "classification"
    result_model = ClassificationResult
