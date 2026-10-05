"""Clustering pipeline and result contract."""

from typing import ClassVar

from pydantic import Field, StrictInt, StrictStr

from ..contracts import AgentResultModel, ModelType
from .base import AgentTypePipeline


class ClusteringResult(AgentResultModel):
    cluster_id: StrictStr | StrictInt
    membership_score: float | None = Field(default=None, ge=0, le=1)


class ClusteringPipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "clustering"
    result_model = ClusteringResult
