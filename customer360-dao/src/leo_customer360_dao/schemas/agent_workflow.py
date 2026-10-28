"""API schemas for ordered segment AI-agent workflows."""

import uuid
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CRON_MACROS = {"@annually", "@daily", "@hourly", "@monthly", "@reboot", "@weekly", "@yearly"}
_CRON_FIELD = re.compile(r"^[0-9*/?,A-Za-z#LWH\-]+$")
MAX_CANDIDATE_CONTENT_ITEMS = 1000


def _validate_candidate_ids(value: list[uuid.UUID]) -> list[uuid.UUID]:
    if len(value) > MAX_CANDIDATE_CONTENT_ITEMS:
        raise ValueError(
            "A workflow step may reference at most "
            f"{MAX_CANDIDATE_CONTENT_ITEMS} content items"
        )
    if len(value) != len(set(value)):
        raise ValueError("candidate_content_item_ids must contain unique IDs")
    return value


def _validate_schedule(value: str | None) -> str | None:
    """Accept a blank override as inheritance and validate common cron shapes."""
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.lower() in _CRON_MACROS:
        return normalized.lower()
    fields = normalized.split()
    if len(fields) != 5 or any(not _CRON_FIELD.fullmatch(field) for field in fields):
        raise ValueError("schedule_definition must be a valid five-field cron expression")
    return normalized


class AgentWorkflowStepBase(BaseModel):
    agent_code: str = Field(..., min_length=1, max_length=100)
    execution_order: int = Field(..., gt=0)
    is_active: bool = True
    schedule_definition: str | None = Field(default=None, max_length=100)
    candidate_content_item_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=MAX_CANDIDATE_CONTENT_ITEMS
    )
    configuration: dict[str, Any] = Field(default_factory=dict)

    @field_validator("candidate_content_item_ids")
    @classmethod
    def candidate_ids_are_unique_and_bounded(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return _validate_candidate_ids(value)

    @field_validator("schedule_definition")
    @classmethod
    def schedule_is_valid(cls, value: str | None) -> str | None:
        return _validate_schedule(value)


class AgentWorkflowStepCreate(AgentWorkflowStepBase):
    """Payload for adding one agent step to a segment."""


class AgentWorkflowStepUpdate(BaseModel):
    """Partial update payload for one workflow step."""

    agent_code: str | None = Field(default=None, min_length=1, max_length=100)
    execution_order: int | None = Field(default=None, gt=0)
    is_active: bool | None = None
    schedule_definition: str | None = Field(default=None, max_length=100)
    candidate_content_item_ids: list[uuid.UUID] | None = Field(
        default=None, max_length=MAX_CANDIDATE_CONTENT_ITEMS
    )
    configuration: dict[str, Any] | None = None

    @field_validator("candidate_content_item_ids")
    @classmethod
    def candidate_ids_are_unique_and_bounded(cls, value: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
        return None if value is None else _validate_candidate_ids(value)

    @field_validator("schedule_definition")
    @classmethod
    def schedule_is_valid(cls, value: str | None) -> str | None:
        return _validate_schedule(value)


class AgentWorkflowReplace(BaseModel):
    """Complete ordered workflow submitted atomically by the editor."""

    steps: list[AgentWorkflowStepBase] = Field(..., max_length=100)


class AgentWorkflowStepRead(AgentWorkflowStepBase):
    """Workflow step plus read-only agent metadata."""

    model_config = ConfigDict(from_attributes=True)

    agent_workflow_id: uuid.UUID
    segment_id: uuid.UUID
    tenant_id: uuid.UUID
    agent_display_name: str
    agent_model_type: str
    agent_status: str
    agent_schedule_definition: str | None = None
    effective_schedule_definition: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
