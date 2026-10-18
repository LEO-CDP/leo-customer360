"""API schemas for campaign workflows."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel

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
    workflow: dict[str, Any] | None
