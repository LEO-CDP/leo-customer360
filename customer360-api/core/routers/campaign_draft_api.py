"""AI Campaign Strategy and Draft Creation endpoints (generate/content-items/
edit/approve/reject/history) -- see
specs/002-ai-campaign-draft-creation/contracts/campaign-drafts-api.md.

Kept separate from crm_api.py's generic CRUD campaigns_router (whose
unguarded PATCH /campaigns/{id} has no approval-state-machine awareness --
see that feature's research.md §5).
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
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
    CampaignDraftStaleError,
    GENERAL_FIELDS,
    UNSET,
    CampaignDraftValidationError,
    CampaignSegmentNotFoundError,
    CampaignTemplateNotFoundError,
)
from core.repositories.campaign_planner_agent import AgentConfigurationInvalidError
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
    """Read the authenticated user identifier from request middleware state."""
    user_id = getattr(request.state, "user_id", None)
    return uuid.UUID(str(user_id)) if user_id else None


def _build_response(repo: CampaignDraftRepository, campaign) -> CampaignDraftResponse:
    """The full non-embedding campaign, its ordered content plan and the
    immutable planner snapshot."""
    data = CampaignDraftResponse.model_validate(campaign).model_dump(exclude={"content_items"})
    data["content_items"] = repo.list_campaign_content_items(campaign.tenant_id, campaign.campaign_id)
    return CampaignDraftResponse.model_validate(data)


def _agent_blocked(exc: AgentConfigurationInvalidError) -> HTTPException:
    """Structured refusal for an unusable planner; no campaign was written."""
    return HTTPException(
        status_code=409,
        detail={"code": "agent_configuration_invalid", "agent_code": exc.agent_code, "reasons": exc.reasons},
    )


def _planned_draft_values(payload) -> dict:
    """Planner selection plus the marketer values the plan must not replace."""
    return payload.model_dump(
        include={"agent_code", "budget_time_constraints", "name", "campaign_code", "start_date", "end_date", "budget_amount", "currency"}
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
            template_id=payload.template_id,
            objective=payload.objective,
            **_planned_draft_values(payload),
        )
    except (CampaignSegmentNotFoundError, CampaignTemplateNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AgentConfigurationInvalidError as exc:
        raise _agent_blocked(exc) from exc
    except (CampaignDraftValidationError, CampaignDraftConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    invalidate_prefix("crm_campaign")
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
            **_planned_draft_values(payload),
        )
    except CampaignSegmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AgentConfigurationInvalidError as exc:
        raise _agent_blocked(exc) from exc
    except (CampaignDraftValidationError, CampaignDraftConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    invalidate_prefix("crm_campaign")
    return _build_response(repo, campaign)


@router.get("/draft/template-options")
def list_template_options(
    request: Request,
    channel: str = Query("email", pattern="^(email|zalo_zns)$"),
    db: Session = Depends(get_db),
):
    """Approved templates the caller's tenant can plan a draft with."""
    tenant_id = uuid.UUID(require_tenant(request))
    return jsonable_encoder(CampaignDraftRepository(db).list_approved_templates(tenant_id, channel))


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
    """The governed campaign editor: every editable field in one guarded,
    audited write with an ``updated_at`` precondition. A governed change
    returns an approved or rejected campaign to InReview."""
    tenant_id = uuid.UUID(require_tenant(request))
    editor_id = _current_user_id(request)
    repo = CampaignDraftRepository(db)

    content_items = (
        [item.model_dump(exclude_unset=True) for item in payload.content_items]
        if payload.content_items is not None
        else None
    )
    sent = payload.model_fields_set
    # ponytail: general fields can be changed but not cleared to null; add an
    # explicit clear list if the editor needs it.
    general_fields = {
        field: getattr(payload, field)
        for field in GENERAL_FIELDS
        if field in sent and getattr(payload, field) is not None
    }
    try:
        campaign = repo.edit_draft(
            tenant_id,
            campaign_id,
            editor_id,
            segment_id=payload.segment_id if "segment_id" in sent else UNSET,
            objective=payload.objective,
            strategy_summary=payload.strategy_summary,
            start_date=payload.start_date,
            end_date=payload.end_date,
            content_items=content_items,
            expected_updated_at=payload.updated_at,
            template_id=payload.template_id if "template_id" in sent else UNSET,
            general_fields=general_fields,
            editor_context=payload.metadata.editor_context if payload.metadata is not None else UNSET,
            replan=payload.replan,
            agent_code=payload.agent_code,
        )
    except CampaignDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CampaignDraftActorNotFoundError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except CampaignDraftValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AgentConfigurationInvalidError as exc:
        raise _agent_blocked(exc) from exc
    except CampaignDraftStaleError as exc:
        current = _build_response(repo, repo.get_campaign(tenant_id, campaign_id))
        raise HTTPException(
            status_code=409,
            detail={"code": "stale_update", "message": str(exc), "current": jsonable_encoder(current)},
        ) from exc
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
