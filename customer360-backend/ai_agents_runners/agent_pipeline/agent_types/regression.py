"""Regression pipeline and result contract."""

from typing import ClassVar

from pydantic import FiniteFloat

from ..contracts import AgentResultModel, ModelType
from .base import AgentTypePipeline


class RegressionResult(AgentResultModel):
    value: FiniteFloat


class RegressionPipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "regression"
    result_model = RegressionResult
