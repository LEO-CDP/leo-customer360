"""API schemas for campaign workflows."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from .crm import CampaignCreate, CampaignRead
from leo_customer360_dao.workflow.models import FlowDefinition


class CampaignWorkflowSaveRequest(BaseModel):
    """Campaign editor payload persisted together with its journey definition."""

    campaign: CampaignCreate
    workflow: FlowDefinition


class CampaignWorkflowSaveResponse(BaseModel):
    """Identifiers and campaign data returned after an atomic save."""

    campaign: CampaignRead
    workflow_id: UUID
    workflow_version: int


class CampaignWorkflowReadResponse(BaseModel):
    """The saved journey version linked to a campaign, if one exists."""

    campaign_id: UUID
    workflow_id: UUID | None
    workflow_version: int | None
    approval_status: str | None = None
    workflow: dict[str, Any] | None


class WorkflowReviewRequest(BaseModel):
    """A human decision on an agent-proposed workflow version."""

    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)


class WorkflowReviewResponse(BaseModel):
    workflow_id: UUID
    workflow_version: int
    approval_status: str
