"""Semantic-embedding pipeline and result contract."""

from typing import ClassVar

from pydantic import FiniteFloat, Field

from ..contracts import AgentResultModel, ModelType
from .base import AgentTypePipeline


class SemanticEmbeddingResult(AgentResultModel):
    embedding: list[FiniteFloat] = Field(min_length=1)


class SemanticEmbeddingPipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "semantic_embedding"
    result_model = SemanticEmbeddingResult
