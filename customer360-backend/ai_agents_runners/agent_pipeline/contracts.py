"""Validated input and output contracts shared by agent-type pipelines."""

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

_RESULT_FIELDS = {
    "classification": {"label"},
    "regression": {"value"},
    "clustering": {"cluster_id"},
    "ranking_recommendation": {"ranked_items"},
    "forecasting": {"forecast_series"},
    "anomaly_detection": {"anomaly_score", "is_anomaly"},
    "uplift_modeling": {"uplift_score"},
    "semantic_embedding": {"embedding"},
    "graph_ml": {"relationship_signals"},
    "optimization": {"selected_action"},
    "rules_engine": {"decision", "decision_trace"},
    "generative_llm": set(),
}


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


def _require_finite_number(result: dict[str, Any], key: str) -> None:
    value = result.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"result.{key} must be a finite number")


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
    """Validated, auditable envelope returned by a completed pipeline."""

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
    def validate_result(self) -> AgentPipelineOutput:
        _validate_json_object("result", self.result)
        if (
            self.completed_at.tzinfo is None
            or self.completed_at.utcoffset() != timezone.utc.utcoffset(self.completed_at)
        ):
            raise ValueError("completed_at must be UTC")
        required_fields = _RESULT_FIELDS[self.model_type]
        missing_fields = required_fields.difference(self.result)
        if missing_fields:
            fields = ", ".join(sorted(missing_fields))
            raise ValueError(f"{self.model_type} result is missing required field(s): {fields}")

        if self.model_type == "classification":
            if not isinstance(self.result["label"], (str, int)) or isinstance(
                self.result["label"], bool
            ):
                raise ValueError("result.label must be a string or integer")
            if "probability" in self.result:
                _require_finite_number(self.result, "probability")
                if not 0 <= self.result["probability"] <= 1:
                    raise ValueError("result.probability must be between 0 and 1")
            if "confidence" in self.result:
                _require_finite_number(self.result, "confidence")
                if not 0 <= self.result["confidence"] <= 1:
                    raise ValueError("result.confidence must be between 0 and 1")
        elif self.model_type == "regression":
            _require_finite_number(self.result, "value")
        elif self.model_type == "clustering":
            if not isinstance(self.result["cluster_id"], (str, int)) or isinstance(
                self.result["cluster_id"], bool
            ):
                raise ValueError("result.cluster_id must be a string or integer")
            if "membership_score" in self.result:
                _require_finite_number(self.result, "membership_score")
                if not 0 <= self.result["membership_score"] <= 1:
                    raise ValueError("result.membership_score must be between 0 and 1")
        elif self.model_type in {"ranking_recommendation", "forecasting"}:
            key = "ranked_items" if self.model_type == "ranking_recommendation" else "forecast_series"
            if not isinstance(self.result[key], list):
                raise ValueError(f"result.{key} must be a list")
        elif self.model_type == "anomaly_detection":
            _require_finite_number(self.result, "anomaly_score")
            if not isinstance(self.result["is_anomaly"], bool):
                raise ValueError("result.is_anomaly must be a boolean")
        elif self.model_type == "uplift_modeling":
            _require_finite_number(self.result, "uplift_score")
        elif self.model_type == "semantic_embedding":
            embedding = self.result["embedding"]
            if not isinstance(embedding, list) or not embedding:
                raise ValueError("result.embedding must be a non-empty list")
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in embedding
            ):
                raise ValueError("result.embedding must contain finite numbers")
        elif self.model_type == "graph_ml":
            if not isinstance(self.result["relationship_signals"], dict):
                raise ValueError("result.relationship_signals must be an object")
        elif self.model_type == "optimization":
            if not isinstance(self.result["selected_action"], str):
                raise ValueError("result.selected_action must be a string")
        elif self.model_type == "rules_engine":
            decision = self.result["decision"]
            if not isinstance(decision, str) or decision not in {"allow", "deny", "review"}:
                raise ValueError("result.decision must be allow, deny, or review")
            if not isinstance(self.result["decision_trace"], list):
                raise ValueError("result.decision_trace must be a list")
        elif self.model_type == "generative_llm":
            if not any(self.result.get(key) is not None for key in ("text", "structured_output", "summary")):
                raise ValueError(
                    "generative_llm result must include text, structured_output, or summary"
                )
            for key in ("text", "summary"):
                if key in self.result and not isinstance(self.result[key], str):
                    raise ValueError(f"result.{key} must be a string")
            if "structured_output" in self.result and not isinstance(
                self.result["structured_output"], dict
            ):
                raise ValueError("result.structured_output must be an object")
        return self
