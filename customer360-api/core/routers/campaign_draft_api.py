"""AI Campaign Strategy and Draft Creation endpoints (generate/content-items/
edit/approve/reject/history) -- see
specs/002-ai-campaign-draft-creation/contracts/campaign-drafts-api.md.

Kept separate from crm_api.py's generic CRUD campaigns_router (whose
unguarded PATCH /campaigns/{id} has no approval-state-machine awareness --
see that feature's research.md §5).
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from core.auth import require_tenant, require_tenant_admin
from core.database import get_db
from core.repositories.campaign_draft_repository import (
    CampaignDraftApprovalBlockedError,
    CampaignDraftConflictError,
    CampaignDraftNotFoundError,
    CampaignDraftRepository,
    CampaignDraftValidationError,
    CampaignSegmentNotFoundError,
    CampaignTemplateNotFoundError,
)
from leo_customer360_dao.schemas.crm import (
    CampaignDraftContentItemRead,
    CampaignDraftRequest,
    CampaignDraftResponse,
    EditCampaignDraftRequest,
    RejectCampaignDraftRequest,
    ZnsCampaignDraftRequest,
)

router = APIRouter(prefix="/campaigns", tags=["AI Campaign Drafts"])


def _current_user_id(request: Request) -> Optional[uuid.UUID]:
    user_id = getattr(request.state, "user_id", None)
    return uuid.UUID(str(user_id)) if user_id else None


def _build_response(repo: CampaignDraftRepository, campaign) -> CampaignDraftResponse:
    content_items = repo.list_campaign_content_items(campaign.tenant_id, campaign.campaign_id)
    return CampaignDraftResponse(
        campaign_id=campaign.campaign_id,
        status=campaign.status,
        approval_status=campaign.approval_status,
        segment_id=campaign.segment_id,
        template_id=campaign.template_id,
        name=campaign.name,
        objective=campaign.objective,
        strategy_summary=campaign.strategy_summary,
        ai_plan=campaign.ai_plan,
        start_date=campaign.start_date,
        end_date=campaign.end_date,
        approved_by=campaign.approved_by,
        approved_at=campaign.approved_at,
        content_items=content_items,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


@router.post("/draft", response_model=CampaignDraftResponse, status_code=201)
def generate_campaign_draft(
    payload: CampaignDraftRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    tenant_id = uuid.UUID(require_tenant(request))
    created_by = _current_user_id(request)
    repo = CampaignDraftRepository(db)

    try:
        campaign = repo.create_draft(
            tenant_id=tenant_id,
            created_by=created_by,
            segment_id=payload.segment_id,
            template_id=payload.template_id,
            objective=payload.objective,
            budget_time_constraints=payload.budget_time_constraints,
        )
    except (CampaignSegmentNotFoundError, CampaignTemplateNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CampaignDraftValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _build_response(repo, campaign)


@router.post("/zalo-draft", response_model=CampaignDraftResponse, status_code=201)
def generate_zns_campaign_draft(
    payload: ZnsCampaignDraftRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """AI Zalo ZNS draft: the AI picks one Approved ZNS template + fills its typed
    params, and a channel='zalo_zns' campaign draft is created InReview (behind the
    same human-approval gate as email drafts)."""
    tenant_id = uuid.UUID(require_tenant(request))
    created_by = _current_user_id(request)
    repo = CampaignDraftRepository(db)

    try:
        campaign = repo.create_zns_draft(
            tenant_id=tenant_id,
            created_by=created_by,
            segment_id=payload.segment_id,
            objective=payload.objective,
            budget_time_constraints=payload.budget_time_constraints,
        )
    except CampaignSegmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CampaignDraftValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _build_response(repo, campaign)


@router.get("/{campaign_id}/content-items", response_model=list[CampaignDraftContentItemRead])
def list_campaign_content_items(campaign_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
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
    tenant_id = uuid.UUID(require_tenant(request))
    editor_id = _current_user_id(request)
    repo = CampaignDraftRepository(db)

    content_items = (
        [item.model_dump(exclude_unset=True) for item in payload.content_items]
        if payload.content_items is not None
        else None
    )
    try:
        campaign = repo.edit_draft(
            tenant_id,
            campaign_id,
            editor_id,
            objective=payload.objective,
            strategy_summary=payload.strategy_summary,
            start_date=payload.start_date,
            end_date=payload.end_date,
            content_items=content_items,
        )
    except CampaignDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CampaignDraftValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CampaignDraftConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _build_response(repo, campaign)


@router.post("/{campaign_id}/approve", response_model=CampaignDraftResponse)
def approve_campaign_draft(campaign_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
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

    return _build_response(repo, campaign)


@router.post("/{campaign_id}/reject", response_model=CampaignDraftResponse)
def reject_campaign_draft(
    campaign_id: uuid.UUID,
    payload: RejectCampaignDraftRequest,
    request: Request,
    db: Session = Depends(get_db),
):
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

    return _build_response(repo, campaign)


@router.get("/{campaign_id}/history")
def list_campaign_history(campaign_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    tenant_id = uuid.UUID(require_tenant(request))
    repo = CampaignDraftRepository(db)
    try:
        repo.get_campaign(tenant_id, campaign_id)  # 404 if not found/wrong tenant
    except CampaignDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return repo.list_campaign_history(tenant_id, campaign_id)


all_campaign_draft_routers = [router]
