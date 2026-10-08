"""Typed contracts and placeholder handlers for Customer 360 agent pipelines."""

from .contracts import AgentPipelineInput, AgentPipelineOutput, ModelType
from .pipelines import execute_agent_pipeline, execute_agent_pipeline_batch

__all__ = [
    "AgentPipelineInput",
    "AgentPipelineOutput",
    "ModelType",
    "execute_agent_pipeline",
    "execute_agent_pipeline_batch",
]
