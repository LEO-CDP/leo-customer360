"""Campaign endpoints that save and load their linked customer journey."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.auth import require_tenant, require_tenant_admin
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
    WorkflowReviewRequest,
    WorkflowReviewResponse,
)
from leo_customer360_dao.workflow import FlowNotFound, WorkflowRepository, WorkflowRuleViolation

router = APIRouter(prefix="/campaigns", tags=["Campaign Workflows"])
review_router = APIRouter(prefix="/workflows", tags=["Campaign Workflows"])
logger = logging.getLogger(__name__)


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
    if isinstance(error, CampaignDraftConflictError | WorkflowRuleViolation):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, CampaignDraftValidationError | CampaignWorkflowValidationError | ValueError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    if isinstance(error, IntegrityError):
        db.rollback()
        original = getattr(error, "orig", None)
        diagnostic = getattr(original, "diag", None)
        constraint = getattr(diagnostic, "constraint_name", None)
        if constraint in {"crm_flow_version_creator_fk", "crm_flow_version_reviewer_fk"}:
            logger.warning(
                "Workflow identity is not a member of the active tenant: constraint=%s",
                constraint,
            )
            raise HTTPException(
                status_code=401,
                detail=(
                    "Your signed-in user no longer exists in the active tenant. "
                    "Sign out and sign in again before saving or reviewing this workflow."
                ),
            ) from error
        detail = {
            "uq_crm_campaign_code": (
                "Campaign code already exists for this tenant. Choose a different campaign code."
            ),
            "crm_flow_tenant_name_key": (
                "A workflow with this campaign's workflow name already exists. Refresh and contact support."
            ),
            "crm_campaign_workflow_version_fk": (
                "The pinned workflow version conflicts with the campaign tenant or workflow. Refresh and retry."
            ),
        }.get(constraint, "The campaign or workflow conflicts with existing data.")
        logger.warning(
            "Campaign/workflow integrity conflict: constraint=%s sqlstate=%s",
            constraint or "unknown",
            getattr(original, "pgcode", None) or getattr(diagnostic, "sqlstate", None) or "unknown",
        )
        raise HTTPException(
            status_code=409,
            detail=detail,
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
        campaign, workflow, version_info = _repository(request, db).load(campaign_id)
    except Exception as exc:
        _raise_http_error(exc, db)
    return CampaignWorkflowReadResponse(
        campaign_id=campaign.campaign_id,
        workflow_id=campaign.workflow_id,
        workflow_version=campaign.workflow_version,
        approval_status=version_info["approval_status"] if version_info else None,
        workflow=workflow,
    )


@review_router.post(
    "/{flow_id}/versions/{version}/review",
    response_model=WorkflowReviewResponse,
)
def review_workflow_version(
    flow_id: uuid.UUID,
    version: int,
    payload: WorkflowReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> WorkflowReviewResponse:
    """Approve or reject an agent-proposed workflow version. Only tenant admins may review."""
    tenant_id = uuid.UUID(require_tenant(request))
    require_tenant_admin(request, "workflow version review")
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        status = WorkflowRepository(db, tenant_id).review(
            flow_id,
            version,
            uuid.UUID(str(user_id)),
            payload.decision,
            payload.reason,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        _raise_http_error(exc, db)
    return WorkflowReviewResponse(
        workflow_id=flow_id,
        workflow_version=version,
        approval_status=status,
    )


all_workflow_routers = [router, review_router]
