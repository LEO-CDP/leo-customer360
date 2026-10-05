"""Shared input, result-base, and output-envelope contracts."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ModelType = Literal[
    "classification",
    "regression",
    "clustering",
    "ranking_recommendation",
    "forecasting",
    "anomaly_detection",
    "uplift_modeling",
    "semantic_embedding",
    "graph_ml",
    "optimization",
    "rules_engine",
    "generative_llm",
]


def _validate_json_object(name: str, value: dict[str, Any]) -> None:
    def validate_value(item: Any, path: str) -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError(f"{name}.{path} must be finite")
            return
        if isinstance(item, list):
            for index, child in enumerate(item):
                validate_value(child, f"{path}[{index}]")
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError(f"{name}.{path} object keys must be strings")
                validate_value(child, f"{path}.{key}")
            return
        raise ValueError(f"{name}.{path} must contain only JSON-compatible values")

    validate_value(value, "")


class AgentResultModel(BaseModel):
    """Base configuration shared by model-type-owned result schemas."""

    model_config = ConfigDict(extra="forbid")


class AgentPipelineInput(BaseModel):
    """Tenant-scoped payload accepted by every agent-type pipeline."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    segment_id: UUID
    agent_code: str = Field(min_length=1, max_length=100)
    model_type: ModelType
    input_data: dict[str, Any]
    configuration: dict[str, Any] = Field(default_factory=dict)
    candidate_content_item_ids: list[UUID] = Field(default_factory=list)
    trigger_event: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_payload(self) -> AgentPipelineInput:
        if not self.agent_code.strip():
            raise ValueError("agent_code must not be blank")
        if self.candidate_content_item_ids and self.model_type != "ranking_recommendation":
            raise ValueError(
                "candidate_content_item_ids are only valid for ranking_recommendation"
            )
        if len(self.candidate_content_item_ids) != len(set(self.candidate_content_item_ids)):
            raise ValueError("candidate_content_item_ids must be unique")
        _validate_json_object("input_data", self.input_data)
        _validate_json_object("configuration", self.configuration)
        _validate_json_object("trigger_event", self.trigger_event)
        return self


class AgentPipelineOutput(BaseModel):
    """Validated common envelope returned by a completed pipeline."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    tenant_id: UUID
    segment_id: UUID
    agent_code: str = Field(min_length=1, max_length=100)
    model_type: ModelType
    result: dict[str, Any]
    completed_at: datetime

    @model_validator(mode="after")
    def validate_envelope(self) -> AgentPipelineOutput:
        _validate_json_object("result", self.result)
        if (
            self.completed_at.tzinfo is None
            or self.completed_at.utcoffset() != timezone.utc.utcoffset(self.completed_at)
        ):
            raise ValueError("completed_at must be UTC")
        return self
