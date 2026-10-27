"""AI Campaign Strategy and Draft Creation endpoints (generate/content-items/
edit/approve/reject/history) -- see
specs/002-ai-campaign-draft-creation/contracts/campaign-drafts-api.md.

Kept separate from crm_api.py's generic CRUD campaigns_router (whose
unguarded PATCH /campaigns/{id} has no approval-state-machine awareness --
see that feature's research.md §5).
"""

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from core.auth import require_tenant, require_tenant_admin
from core.cache import invalidate_prefix
from core.database import get_db
from core.repositories.campaign_draft_repository import (
    CampaignDraftApprovalBlockedError,
    CampaignDraftActorNotFoundError,
    CampaignDraftConflictError,
    CampaignDraftNotFoundError,
    CampaignDraftRepository,
    SEGMENT_UNSET,
    CampaignDraftValidationError,
    CampaignSegmentNotFoundError,
)
from leo_customer360_dao.schemas.crm import (
    CampaignDraftContentItemRead,
    CampaignDraftRequest,
    CampaignDraftResponse,
    EditCampaignDraftRequest,
    RejectCampaignDraftRequest,
)

router = APIRouter(prefix="/campaigns", tags=["AI Campaign Drafts"])


def _current_user_id(request: Request) -> Optional[uuid.UUID]:
    """Read the authenticated user identifier or fail with a client-safe 401."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return None
    try:
        return uuid.UUID(str(user_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid authenticated user identity") from exc


def _build_response(repo: CampaignDraftRepository, campaign: Any) -> CampaignDraftResponse:
    """Build the governed draft response from a campaign and its content plan.

    The repository returns ORM objects in production and lightweight
    attribute-shaped objects in API tests. Required campaign identity and
    governance fields remain strict; optional fields use ``getattr`` so older
    ORM-shaped objects remain readable after additive schema changes.
    """
    raw_content_items = repo.list_campaign_content_items(
        campaign.tenant_id,
        campaign.campaign_id,
    )
    content_items = [
        CampaignDraftContentItemRead.model_validate(item)
        for item in (raw_content_items or [])
    ]
    return CampaignDraftResponse(
        campaign_id=campaign.campaign_id,
        status=getattr(campaign, "status", None),
        approval_status=campaign.approval_status,
        segment_id=getattr(campaign, "segment_id", None),
        name=getattr(campaign, "name", None),
        objective=getattr(campaign, "objective", None),
        journey_stage=getattr(campaign, "journey_stage", None),
        strategy_summary=getattr(campaign, "strategy_summary", None),
        planning_constraints=getattr(campaign, "planning_constraints", None),
        ai_plan=getattr(campaign, "ai_plan", None),
        start_date=getattr(campaign, "start_date", None),
        end_date=getattr(campaign, "end_date", None),
        approved_by=getattr(campaign, "approved_by", None),
        approved_at=getattr(campaign, "approved_at", None),
        content_items=content_items,
        created_at=getattr(campaign, "created_at", None),
        updated_at=getattr(campaign, "updated_at", None),
    )


@router.post("/draft", response_model=CampaignDraftResponse, status_code=201)
def generate_campaign_draft(
    payload: CampaignDraftRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Generate and persist an email campaign draft for the current tenant."""
    tenant_id = uuid.UUID(require_tenant(request))
    created_by = _current_user_id(request)
    repo = CampaignDraftRepository(db)

    try:
        campaign = repo.create_draft(
            tenant_id=tenant_id,
            created_by=created_by,
            segment_id=payload.segment_id,
            objective=payload.objective,
            journey_stage=payload.journey_stage,
            budget_time_constraints=payload.budget_time_constraints,
        )
    except CampaignSegmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CampaignDraftValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Campaign reads are cached by the generic CRM routes; invalidate only
    # after the governed write has succeeded.
    invalidate_prefix("crm_campaign")
    return _build_response(repo, campaign)


@router.post("/{campaign_id}/submit-for-approval", response_model=CampaignDraftResponse)
def submit_campaign_for_approval(
    campaign_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    """Submit a complete editor-created Draft campaign to the admin review queue."""
    tenant_id = uuid.UUID(require_tenant(request))
    submitter_id = _current_user_id(request)
    if submitter_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    repo = CampaignDraftRepository(db)

    try:
        campaign = repo.submit_for_review(tenant_id, campaign_id, submitter_id)
    except CampaignDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CampaignDraftValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CampaignDraftConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    invalidate_prefix("crm_campaign")
    return _build_response(repo, campaign)


@router.get("/{campaign_id}/content-items", response_model=list[CampaignDraftContentItemRead])
def list_campaign_content_items(campaign_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    """Return the tenant-scoped content plan for one campaign."""
    tenant_id = uuid.UUID(require_tenant(request))
    repo = CampaignDraftRepository(db)
    try:
        repo.get_campaign(tenant_id, campaign_id)  # 404 if not found/wrong tenant
    except CampaignDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return repo.list_campaign_content_items(tenant_id, campaign_id)


@router.patch("/{campaign_id}/draft", response_model=CampaignDraftResponse)
def edit_campaign_draft(
    campaign_id: uuid.UUID,
    payload: EditCampaignDraftRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Edit a draft and return approved or rejected campaigns to InReview."""
    tenant_id = uuid.UUID(require_tenant(request))
    editor_id = _current_user_id(request)
    repo = CampaignDraftRepository(db)

    content_items = (
        [item.model_dump(exclude_unset=True) for item in payload.content_items]
        if payload.content_items is not None
        else None
    )
    draft_fields = {
        "segment_id": (
            payload.segment_id
            if "segment_id" in payload.model_fields_set
            else SEGMENT_UNSET
        ),
        "content_items": content_items,
    }
    for field in ("objective", "journey_stage", "strategy_summary", "planning_constraints", "start_date", "end_date"):
        if field in payload.model_fields_set:
            draft_fields[field] = getattr(payload, field)
    try:
        campaign = repo.edit_draft(
            tenant_id,
            campaign_id,
            editor_id,
            **draft_fields,
        )
    except CampaignDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CampaignDraftActorNotFoundError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except CampaignDraftValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CampaignDraftConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    invalidate_prefix("crm_campaign")
    return _build_response(repo, campaign)


@router.post("/{campaign_id}/approve", response_model=CampaignDraftResponse)
def approve_campaign_draft(campaign_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    """Approve a campaign after the repository revalidates its dependencies."""
    tenant_id = uuid.UUID(require_tenant(request))
    require_tenant_admin(request, "campaign draft approval")
    reviewer_id = _current_user_id(request)
    if reviewer_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    repo = CampaignDraftRepository(db)

    try:
        campaign = repo.approve(tenant_id, campaign_id, reviewer_id)
    except CampaignDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (CampaignDraftApprovalBlockedError, CampaignDraftConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    invalidate_prefix("crm_campaign")
    return _build_response(repo, campaign)


@router.post("/{campaign_id}/reject", response_model=CampaignDraftResponse)
def reject_campaign_draft(
    campaign_id: uuid.UUID,
    payload: RejectCampaignDraftRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Reject a campaign draft with an optional reviewer reason."""
    tenant_id = uuid.UUID(require_tenant(request))
    require_tenant_admin(request, "campaign draft approval")
    reviewer_id = _current_user_id(request)
    if reviewer_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    repo = CampaignDraftRepository(db)

    try:
        campaign = repo.reject(tenant_id, campaign_id, reviewer_id, reason=payload.reason)
    except CampaignDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (CampaignDraftApprovalBlockedError, CampaignDraftConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    invalidate_prefix("crm_campaign")
    return _build_response(repo, campaign)


@router.get("/{campaign_id}/history")
def list_campaign_history(campaign_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    """Return the merged audit and review timeline for one campaign."""
    tenant_id = uuid.UUID(require_tenant(request))
    repo = CampaignDraftRepository(db)
    try:
        repo.get_campaign(tenant_id, campaign_id)  # 404 if not found/wrong tenant
    except CampaignDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return repo.list_campaign_history(tenant_id, campaign_id)


all_campaign_draft_routers = [router]
