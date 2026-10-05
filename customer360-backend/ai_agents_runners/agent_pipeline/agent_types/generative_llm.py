"""Generative-LLM pipeline and result contract."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import StrictStr, model_validator

from ..contracts import AgentResultModel, ModelType
from .base import AgentTypePipeline


class GenerativeLlmResult(AgentResultModel):
    text: StrictStr | None = None
    structured_output: dict[str, Any] | None = None
    summary: StrictStr | None = None

    @model_validator(mode="after")
    def require_output(self) -> GenerativeLlmResult:
        if not any(
            value is not None
            for value in (self.text, self.structured_output, self.summary)
        ):
            raise ValueError(
                "generative_llm result must include text, structured_output, or summary"
            )
        return self


class GenerativeLlmPipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "generative_llm"
    result_model = GenerativeLlmResult
