"""Anomaly-detection pipeline and result contract."""

from typing import ClassVar

from pydantic import FiniteFloat, StrictBool

from ..contracts import AgentResultModel, ModelType
from .base import AgentTypePipeline


class AnomalyDetectionResult(AgentResultModel):
    anomaly_score: FiniteFloat
    is_anomaly: StrictBool


class AnomalyDetectionPipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "anomaly_detection"
    result_model = AnomalyDetectionResult
