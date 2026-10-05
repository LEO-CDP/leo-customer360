"""Common strategy interface for model-type-specific agent pipelines."""

from __future__ import annotations

from typing import Any, ClassVar

from ..contracts import AgentPipelineInput, AgentResultModel, ModelType


class AgentTypePipeline:
    """Validate and run one model-type strategy."""

    model_type: ClassVar[ModelType]
    result_model: ClassVar[type[AgentResultModel]]

    def __call__(self, payload: AgentPipelineInput) -> dict[str, Any]:
        if payload.model_type != self.model_type:
            raise ValueError(
                f"{self.__class__.__name__} cannot handle {payload.model_type}"
            )
        result = self.process(payload)
        validated_result = self.result_model.model_validate(result)
        return validated_result.model_dump(mode="json")

    def process(self, payload: AgentPipelineInput) -> dict[str, Any]:
        """Implement the model-type processing step in a concrete strategy."""
        raise NotImplementedError(
            f"The {self.model_type} agent pipeline is a scaffold; implement its "
            "processing handler before enabling this run."
        )
