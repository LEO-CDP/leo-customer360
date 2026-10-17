"""Campaign endpoints that save and load their linked customer journey."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.auth import require_tenant
from core.cache import invalidate_prefix
from core.database import get_db
from core.repositories.campaign_draft_repository import (
    CampaignDraftActorNotFoundError,
    CampaignDraftConflictError,
    CampaignDraftValidationError,
)
from core.repositories.campaign_workflow_repository import (
    CampaignWorkflowNotFound,
    CampaignWorkflowRepository,
    CampaignWorkflowValidationError,
)
from leo_customer360_dao.schemas.crm import CampaignRead
from leo_customer360_dao.schemas.workflow import (
    CampaignWorkflowReadResponse,
    CampaignWorkflowSaveRequest,
    CampaignWorkflowSaveResponse,
)
from leo_customer360_dao.workflow import FlowNotFound

router = APIRouter(prefix="/campaigns", tags=["Campaign Workflows"])


def _repository(request: Request, db: Session) -> CampaignWorkflowRepository:
    user_id = getattr(request.state, "user_id", None)
    return CampaignWorkflowRepository(
        db,
        tenant_id=uuid.UUID(require_tenant(request)),
        user_id=uuid.UUID(str(user_id)) if user_id else None,
    )


def _raise_http_error(error: Exception, db: Session) -> None:
    if isinstance(error, PermissionError):
        raise HTTPException(status_code=403, detail=str(error)) from error
    if isinstance(error, CampaignWorkflowNotFound | FlowNotFound):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, CampaignDraftActorNotFoundError):
        raise HTTPException(status_code=401, detail=str(error)) from error
    if isinstance(error, CampaignDraftConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, CampaignDraftValidationError | CampaignWorkflowValidationError | ValueError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    if isinstance(error, IntegrityError):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="The campaign or workflow conflicts with existing data.",
        ) from error
    raise error


def _save_response(campaign) -> CampaignWorkflowSaveResponse:
    return CampaignWorkflowSaveResponse(
        campaign=CampaignRead.model_validate(campaign),
        workflow_id=campaign.workflow_id,
        workflow_version=campaign.workflow_version,
    )


@router.post("/with-workflow", response_model=CampaignWorkflowSaveResponse, status_code=201)
def create_campaign_with_workflow(
    payload: CampaignWorkflowSaveRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> CampaignWorkflowSaveResponse:
    """Create a campaign and its first workflow version in one transaction."""
    try:
        campaign = _repository(request, db).create(payload)
    except Exception as exc:
        _raise_http_error(exc, db)
    invalidate_prefix("crm_campaign")
    return _save_response(campaign)


@router.patch("/{campaign_id}/with-workflow", response_model=CampaignWorkflowSaveResponse)
def update_campaign_with_workflow(
    campaign_id: uuid.UUID,
    payload: CampaignWorkflowSaveRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> CampaignWorkflowSaveResponse:
    """Update campaign fields and save a workflow version atomically."""
    try:
        campaign = _repository(request, db).update(campaign_id, payload)
    except Exception as exc:
        _raise_http_error(exc, db)
    invalidate_prefix("crm_campaign")
    return _save_response(campaign)


@router.get("/{campaign_id}/workflow", response_model=CampaignWorkflowReadResponse)
def get_campaign_workflow(
    campaign_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> CampaignWorkflowReadResponse:
    """Load the exact workflow version associated with the tenant-owned campaign."""
    try:
        campaign, workflow = _repository(request, db).load(campaign_id)
    except Exception as exc:
        _raise_http_error(exc, db)
    return CampaignWorkflowReadResponse(
        campaign_id=campaign.campaign_id,
        workflow_id=campaign.workflow_id,
        workflow_version=campaign.workflow_version,
        workflow=workflow,
    )


all_workflow_routers = [router]