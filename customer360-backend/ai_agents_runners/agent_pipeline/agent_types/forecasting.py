"""Forecasting pipeline and result contract."""

from typing import Any, ClassVar

from ..contracts import AgentResultModel, ModelType
from .base import AgentTypePipeline


class ForecastingResult(AgentResultModel):
    forecast_series: list[dict[str, Any]]


class ForecastingPipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "forecasting"
    result_model = ForecastingResult
