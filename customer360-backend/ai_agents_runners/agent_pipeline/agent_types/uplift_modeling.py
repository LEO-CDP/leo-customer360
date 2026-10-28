"""Uplift-modeling pipeline and result contract."""

from typing import ClassVar

from pydantic import FiniteFloat

from ..contracts import AgentResultModel, ModelType
from .base import AgentTypePipeline


class UpliftModelingResult(AgentResultModel):
    uplift_score: FiniteFloat


class UpliftModelingPipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "uplift_modeling"
    result_model = UpliftModelingResult
