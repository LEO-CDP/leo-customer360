"""Routers for the Customer Identity Resolution (CIR) core models: master
profiles, raw profile staging, profile links, and the matching-rule
metadata / throttle-status tables consumed by backend-system/identity_resolution.
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from core.cache import cache_response, invalidate_prefix
from core.config import settings
from core.crud import profile360 as profile360_crud
from core.crud.base import CRUDBase
from core.database import get_db
from core.models.identity import (
    CdpIdentityIndex,
    CdpIdResolutionStatus,
    CdpMasterProfile,
    CdpProfileAttribute,
    CdpProfileLink,
    CdpProfileMergeHistory,
    CdpRawProfileStage,
)
from core.routers._generic import build_crud_router
from core.schemas.identity import (
    IdentityIndexCreate,
    IdentityIndexRead,
    IdentityIndexUpdate,
    IdResolutionStatusRead,
    MasterProfileCreate,
    MasterProfileRead,
    MasterProfileUpdate,
    ProfileAttributeCreate,
    ProfileAttributeRead,
    ProfileAttributeUpdate,
    ProfileLinkCreate,
    ProfileLinkRead,
    ProfileMergeHistoryCreate,
    ProfileMergeHistoryRead,
    RawProfileCreate,
    RawProfileRead,
    RawProfileUpdate,
)
from core.schemas.profile360 import ChannelActivity, EngagementSummary, TimelineEntry, TopInterest

# --- Master Profiles ---------------------------------------------------------

master_profiles_router = APIRouter(prefix="/master-profiles", tags=["Identity Resolution - Master Profiles"])
_master_crud = CRUDBase(CdpMasterProfile)


@master_profiles_router.get("/", response_model=list[MasterProfileRead])
@cache_response("master_profiles/list", ttl=settings.cache_ttl_seconds)
def list_master_profiles(
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None, pattern="^(retail|banking|real_estate|travel|media|education)$"),
    lifecycle_stage: Optional[str] = Query(
        default=None, pattern="^(prospect|lead|customer|vip|dormant|churn_risk)$"
    ),
    q: Optional[str] = Query(default=None, description="Free-text search over full_name/persona_name/email"),
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    cutoff = None
    if days is not None:
        cutoff = datetime.utcnow() - timedelta(days=days)

    if q:
        stmt = select(CdpMasterProfile)
        if tenant_id is not None:
            stmt = stmt.where(CdpMasterProfile.tenant_id == tenant_id)
        if domain is not None:
            stmt = stmt.where(CdpMasterProfile.domain == domain)
        if lifecycle_stage is not None:
            stmt = stmt.where(CdpMasterProfile.lifecycle_stage == lifecycle_stage)
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                CdpMasterProfile.full_name.ilike(pattern),
                CdpMasterProfile.persona_name.ilike(pattern),
                CdpMasterProfile.email.ilike(pattern),
                CdpMasterProfile.phone_number.ilike(pattern),
            )
        )
        if cutoff is not None:
            stmt = stmt.where(CdpMasterProfile.created_at >= cutoff)
        stmt = stmt.order_by(CdpMasterProfile.last_activity_at.desc().nullslast()).offset(skip).limit(limit)
        return db.execute(stmt).scalars().all()

    filters = {"tenant_id": tenant_id, "domain": domain, "lifecycle_stage": lifecycle_stage}
    items = _master_crud.list(db, skip=skip, limit=limit, **filters)
    if cutoff is not None:
        items = [item for item in items if item.created_at is not None and item.created_at >= cutoff]
    return items


@master_profiles_router.get("/count")
@cache_response("master_profiles/count", ttl=settings.cache_ttl_seconds)
def count_master_profiles_endpoint(
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None, pattern="^(retail|banking|real_estate|travel|media|education)$"),
    db: Session = Depends(get_db),
):
    return {"count": _master_crud.count(db, tenant_id=tenant_id, domain=domain)}


@master_profiles_router.get("/{master_profile_id}", response_model=MasterProfileRead)
@cache_response("master_profiles/item", ttl=settings.cache_ttl_seconds)
def get_master_profile(master_profile_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = _master_crud.get(db, master_profile_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    return obj


@master_profiles_router.get("/{master_profile_id}/links", response_model=list[ProfileLinkRead])
@cache_response("master_profiles/links", ttl=settings.cache_ttl_seconds)
def get_master_profile_links(
    master_profile_id: uuid.UUID,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    """All raw profiles that were resolved/merged into this master profile.
    Bounded by `limit` (backed by idx_cdp_profile_links_master) so a single
    heavily-merged master profile can never return an unbounded result set."""
    stmt = (
        select(CdpProfileLink)
        .where(CdpProfileLink.master_profile_id == master_profile_id)
        .order_by(CdpProfileLink.created_at.desc())
        .limit(limit)
    )
    return db.execute(stmt).scalars().all()


@master_profiles_router.get("/{master_profile_id}/engagement-summary", response_model=EngagementSummary)
@cache_response("master_profiles/engagement_summary", ttl=settings.cache_ttl_seconds)
def get_master_profile_engagement_summary(
    master_profile_id: uuid.UUID, days: int = Query(default=90, ge=1, le=365), db: Session = Depends(get_db)
):
    """Login/transaction counts, spend, and last-interaction timestamp for the
    last ``days`` days, aggregated from cdp_raw_events + crm_transactions +
    crm_customer_contacts."""
    if _master_crud.get(db, master_profile_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    return profile360_crud.get_engagement_summary(db, master_profile_id, days=days)


@master_profiles_router.get("/{master_profile_id}/channel-activity", response_model=ChannelActivity)
@cache_response("master_profiles/channel_activity", ttl=settings.cache_ttl_seconds)
def get_master_profile_channel_activity(
    master_profile_id: uuid.UUID, days: int = Query(default=90, ge=1, le=365), db: Session = Depends(get_db)
):
    """Cross-channel activity counts (app/web sessions, customer service
    contacts, transactions) for the last ``days`` days."""
    if _master_crud.get(db, master_profile_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    return profile360_crud.get_channel_activity(db, master_profile_id, days=days)


@master_profiles_router.get("/{master_profile_id}/top-interests", response_model=list[TopInterest])
@cache_response("master_profiles/top_interests", ttl=settings.cache_ttl_seconds)
def get_master_profile_top_interests(
    master_profile_id: uuid.UUID, limit: int = Query(default=5, ge=1, le=20), db: Session = Depends(get_db)
):
    """Top behavioral-event categories for this profile (cdp_raw_events.event_category),
    ranked by count and normalized to a percentage of the top category."""
    if _master_crud.get(db, master_profile_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    return profile360_crud.get_top_interests(db, master_profile_id, limit=limit)


@master_profiles_router.get("/{master_profile_id}/timeline", response_model=list[TimelineEntry])
@cache_response("master_profiles/timeline", ttl=settings.cache_ttl_seconds)
def get_master_profile_timeline(
    master_profile_id: uuid.UUID, limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)
):
    """Unified, most-recent-first activity feed merging behavioral events,
    transactions, and logged customer service contacts."""
    if _master_crud.get(db, master_profile_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    return profile360_crud.get_timeline(db, master_profile_id, limit=limit)


@master_profiles_router.post("/", response_model=MasterProfileRead, status_code=201)
def create_master_profile(payload: MasterProfileCreate, db: Session = Depends(get_db)):
    obj = _master_crud.create(db, payload.model_dump())
    invalidate_prefix("master_profiles")
    return obj


@master_profiles_router.patch("/{master_profile_id}", response_model=MasterProfileRead)
def update_master_profile(master_profile_id: uuid.UUID, payload: MasterProfileUpdate, db: Session = Depends(get_db)):
    obj = _master_crud.get(db, master_profile_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    obj = _master_crud.update(db, obj, payload.model_dump(exclude_unset=True))
    invalidate_prefix("master_profiles")
    return obj


@master_profiles_router.delete("/{master_profile_id}", status_code=204)
def delete_master_profile(master_profile_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = _master_crud.get(db, master_profile_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    _master_crud.delete(db, obj)
    invalidate_prefix("master_profiles")


# --- Raw Profiles Stage -------------------------------------------------------

raw_profiles_router = APIRouter(prefix="/raw-profiles", tags=["Identity Resolution - Raw Profiles"])
_raw_crud = CRUDBase(CdpRawProfileStage)


@raw_profiles_router.get("/", response_model=list[RawProfileRead])
@cache_response("raw_profiles/list", ttl=settings.cache_ttl_seconds)
def list_raw_profiles(
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None, pattern="^(retail|banking|real_estate|travel|media|education)$"),
    source_system: Optional[str] = None,
    status_code: Optional[int] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    return _raw_crud.list(
        db,
        skip=skip,
        limit=limit,
        tenant_id=tenant_id,
        domain=domain,
        source_system=source_system,
        status_code=status_code,
    )


@raw_profiles_router.get("/count")
@cache_response("raw_profiles/count", ttl=settings.cache_ttl_seconds)
def count_raw_profiles_endpoint(
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None, pattern="^(retail|banking|real_estate|travel|media|education)$"),
    source_system: Optional[str] = None,
    status_code: Optional[int] = None,
    db: Session = Depends(get_db),
):
    return {
        "count": _raw_crud.count(
            db, tenant_id=tenant_id, domain=domain, source_system=source_system, status_code=status_code
        )
    }


@raw_profiles_router.get("/{raw_profile_id}", response_model=RawProfileRead)
@cache_response("raw_profiles/item", ttl=settings.cache_ttl_seconds)
def get_raw_profile(raw_profile_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = _raw_crud.get(db, raw_profile_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpRawProfileStage '{raw_profile_id}' not found")
    return obj


@raw_profiles_router.post("/", response_model=RawProfileRead, status_code=201)
def create_raw_profile(payload: RawProfileCreate, db: Session = Depends(get_db)):
    """Ingests a raw profile event (status_code defaults to 1 = new/unprocessed,
    ready to be picked up by backend-system/identity_resolution)."""
    obj = _raw_crud.create(db, payload.model_dump())
    invalidate_prefix("raw_profiles")
    return obj


@raw_profiles_router.patch("/{raw_profile_id}", response_model=RawProfileRead)
def update_raw_profile(raw_profile_id: uuid.UUID, payload: RawProfileUpdate, db: Session = Depends(get_db)):
    obj = _raw_crud.get(db, raw_profile_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpRawProfileStage '{raw_profile_id}' not found")
    obj = _raw_crud.update(db, obj, payload.model_dump(exclude_unset=True))
    invalidate_prefix("raw_profiles")
    return obj


@raw_profiles_router.delete("/{raw_profile_id}", status_code=204)
def delete_raw_profile(raw_profile_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = _raw_crud.get(db, raw_profile_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpRawProfileStage '{raw_profile_id}' not found")
    _raw_crud.delete(db, obj)
    invalidate_prefix("raw_profiles")


# --- Profile Links -------------------------------------------------------------

profile_links_router = APIRouter(prefix="/profile-links", tags=["Identity Resolution - Profile Links"])
_link_crud = CRUDBase(CdpProfileLink)


@profile_links_router.get("/", response_model=list[ProfileLinkRead])
@cache_response("profile_links/list", ttl=settings.cache_ttl_seconds)
def list_profile_links(
    tenant_id: Optional[uuid.UUID] = None,
    raw_profile_id: Optional[uuid.UUID] = None,
    master_profile_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    return _link_crud.list(
        db,
        skip=skip,
        limit=limit,
        tenant_id=tenant_id,
        raw_profile_id=raw_profile_id,
        master_profile_id=master_profile_id,
    )


@profile_links_router.get("/{link_id}", response_model=ProfileLinkRead)
@cache_response("profile_links/item", ttl=settings.cache_ttl_seconds)
def get_profile_link(link_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = _link_crud.get(db, link_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpProfileLink '{link_id}' not found")
    return obj


@profile_links_router.post("/", response_model=ProfileLinkRead, status_code=201)
def create_profile_link(payload: ProfileLinkCreate, db: Session = Depends(get_db)):
    obj = _link_crud.create(db, payload.model_dump())
    invalidate_prefix("profile_links")
    return obj


@profile_links_router.delete("/{link_id}", status_code=204)
def delete_profile_link(link_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = _link_crud.get(db, link_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpProfileLink '{link_id}' not found")
    _link_crud.delete(db, obj)
    invalidate_prefix("profile_links")


# --- Profile Attributes (matching-rule metadata) --------------------------------

profile_attributes_router = build_crud_router(
    model=CdpProfileAttribute,
    pk_field="id",
    pk_type=int,
    create_schema=ProfileAttributeCreate,
    update_schema=ProfileAttributeUpdate,
    read_schema=ProfileAttributeRead,
    prefix="/profile-attributes",
    tags=["Identity Resolution - Matching Rules"],
)


# --- Identity Index (flattened O(1) identifier lookup) --------------------------

identity_index_router = build_crud_router(
    model=CdpIdentityIndex,
    pk_field="identity_index_id",
    pk_type=uuid.UUID,
    create_schema=IdentityIndexCreate,
    update_schema=IdentityIndexUpdate,
    read_schema=IdentityIndexRead,
    prefix="/identity-index",
    tags=["Identity Resolution - Identity Index"],
)


# --- Profile Merge History (append-only audit log; no update/delete) -----------

profile_merge_history_router = APIRouter(
    prefix="/profile-merge-history", tags=["Identity Resolution - Merge History"]
)
_merge_history_crud = CRUDBase(CdpProfileMergeHistory)


@profile_merge_history_router.get("/", response_model=list[ProfileMergeHistoryRead])
@cache_response("profile_merge_history/list", ttl=settings.cache_ttl_seconds)
def list_profile_merge_history(
    tenant_id: Optional[uuid.UUID] = None,
    target_master_profile_id: Optional[uuid.UUID] = None,
    source_master_profile_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    return _merge_history_crud.list(
        db,
        skip=skip,
        limit=limit,
        tenant_id=tenant_id,
        target_master_profile_id=target_master_profile_id,
        source_master_profile_id=source_master_profile_id,
    )


@profile_merge_history_router.get("/{merge_id}", response_model=ProfileMergeHistoryRead)
@cache_response("profile_merge_history/item", ttl=settings.cache_ttl_seconds)
def get_profile_merge_history(merge_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = _merge_history_crud.get(db, merge_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpProfileMergeHistory '{merge_id}' not found")
    return obj


@profile_merge_history_router.post("/", response_model=ProfileMergeHistoryRead, status_code=201)
def create_profile_merge_history(payload: ProfileMergeHistoryCreate, db: Session = Depends(get_db)):
    obj = _merge_history_crud.create(db, payload.model_dump())
    invalidate_prefix("profile_merge_history")
    return obj


# --- Resolution status (real-time throttle state) -------------------------------

resolution_status_router = APIRouter(prefix="/resolution-status", tags=["Identity Resolution - Matching Rules"])


@resolution_status_router.get("/", response_model=IdResolutionStatusRead)
def get_resolution_status(db: Session = Depends(get_db)):
    obj = db.get(CdpIdResolutionStatus, True)
    if obj is None:
        raise HTTPException(
            status_code=404,
            detail="cdp_id_resolution_status has not been initialized yet "
            "(run backend-system/identity_resolution/scripts/init_sample_data.py).",
        )
    return obj


all_identity_routers = [
    master_profiles_router,
    raw_profiles_router,
    profile_links_router,
    profile_attributes_router,
    identity_index_router,
    profile_merge_history_router,
    resolution_status_router,
]
