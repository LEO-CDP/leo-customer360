"""Routers for the Customer Identity Resolution (CIR) core models: master
profiles, raw profile staging, profile links, and the matching-rule
metadata / throttle-status tables consumed by backend-system/identity_resolution.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.cache import cache_response, invalidate_prefix
from core.config import settings
from core.crud import identity as identity_crud
from core.crud import profile360 as profile360_crud
from core.crud.base import CRUDBase
from core.database import get_db
from core.models.identity import (
    CdpCustomerPersona,
    CdpIdentityIndex,
    CdpIdResolutionStatus,
    CdpMasterProfile,
    CdpPersonaFeature,
    CdpPersonaHistory,
    CdpPersonaScoreDetail,
    CdpProfileAttribute,
    CdpProfileLink,
    CdpProfileMergeHistory,
    CdpRawProfileStage,
)
from core.routers._generic import build_crud_router
from core.schemas.identity import (
    CustomerPersonaCreate,
    CustomerPersonaRead,
    CustomerPersonaUpdate,
    IdentityIndexCreate,
    IdentityIndexRead,
    IdentityIndexUpdate,
    IdResolutionStatusRead,
    LinkedRawProfileDetailRead,
    MasterProfileCreate,
    MasterProfileListResponse,
    MasterProfileRead,
    MasterProfileUpdate,
    PersonaFeatureCreate,
    PersonaFeatureRead,
    PersonaHistoryCreate,
    PersonaHistoryRead,
    PersonaAnalyticsSummary,
    PersonaScoreDetailCreate,
    PersonaScoreDetailRead,
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
from core.utils.domains import validate_domain_value

# --- Master Profiles ---------------------------------------------------------

master_profiles_router = APIRouter(prefix="/master-profiles", tags=["Identity Resolution - Master Profiles"])
_master_crud = CRUDBase(CdpMasterProfile)


@master_profiles_router.get("/", response_model=MasterProfileListResponse)
@cache_response("master_profiles/list", ttl=settings.cache_ttl_seconds)
def list_master_profiles(
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None),
    lifecycle_stage: Optional[str] = Query(
        default=None, pattern="^(prospect|lead|customer|vip|dormant|churn_risk)$"
    ),
    domain_attribute_key: Optional[str] = Query(
        default=None,
        description="Generic key in cdp_domain_profiles.domain_attributes used for filtering.",
    ),
    domain_attribute_value: Optional[str] = Query(
        default=None,
        description="Expected value for domain_attribute_key in cdp_domain_profiles.domain_attributes.",
    ),
    membership_tier: Optional[str] = Query(default=None),
    clv_segment: Optional[str] = Query(default=None),
    churn_risk_tier: Optional[str] = Query(default=None, pattern="^(low|medium|high|critical)$"),
    linked_raw_profile_count_min: Optional[int] = Query(default=None, ge=0),
    q: Optional[str] = Query(default=None, description="Free-text search over full_name/persona_name/email"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=settings.api_default_page_size, ge=1, le=settings.api_max_page_size),
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    try:
        validate_domain_value(db, domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return identity_crud.list_master_profiles_page(
        db,
        tenant_id=tenant_id,
        domain=domain,
        lifecycle_stage=lifecycle_stage,
        domain_attribute_key=domain_attribute_key,
        domain_attribute_value=domain_attribute_value,
        membership_tier=membership_tier,
        clv_segment=clv_segment,
        churn_risk_tier=churn_risk_tier,
        linked_raw_profile_count_min=linked_raw_profile_count_min,
        q=q,
        days=days,
        page=page,
        page_size=page_size,
    )


@master_profiles_router.get("/count")
@cache_response("master_profiles/count", ttl=settings.cache_ttl_seconds)
def count_master_profiles_endpoint(
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    try:
        validate_domain_value(db, domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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


@master_profiles_router.get(
    "/{master_profile_id}/linked-raw-profiles/{raw_profile_id}", response_model=LinkedRawProfileDetailRead
)
@cache_response("master_profiles/linked_raw_profile_detail", ttl=settings.cache_ttl_seconds)
def get_master_profile_linked_raw_profile_detail(
    master_profile_id: uuid.UUID,
    raw_profile_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """Detailed view of a linked raw profile scoped to a single master profile.

    Uses master_profile_id + raw_profile_id and enforces tenant-scoped joins so
    linked-raw detail cannot be fetched across tenants.
    """
    master_profile = _master_crud.get(db, master_profile_id)
    if master_profile is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")

    stmt = (
        select(CdpProfileLink, CdpRawProfileStage)
        .join(CdpRawProfileStage, CdpRawProfileStage.raw_profile_id == CdpProfileLink.raw_profile_id)
        .where(
            CdpProfileLink.master_profile_id == master_profile_id,
            CdpProfileLink.raw_profile_id == raw_profile_id,
            CdpProfileLink.tenant_id == master_profile.tenant_id,
            CdpRawProfileStage.tenant_id == master_profile.tenant_id,
        )
        .limit(1)
    )
    row = db.execute(stmt).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Raw profile '{raw_profile_id}' is not linked to master profile "
                f"'{master_profile_id}'"
            ),
        )

    link, raw_profile = row
    return {"link": link, "raw_profile": raw_profile}


@master_profiles_router.get("/{master_profile_id}/persona", response_model=CustomerPersonaRead)
@cache_response("master_profiles/persona", ttl=settings.cache_ttl_seconds)
def get_master_profile_current_persona(master_profile_id: uuid.UUID, db: Session = Depends(get_db)):
    """The profile's CURRENT persona (identity *understanding*, computed from
    the resolved identity by backend-system/identity_resolution's
    PersonaResolutionEngine), resolved via current_persona_id. 404 if the
    profile has no persona computed yet."""
    profile = _master_crud.get(db, master_profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    if profile.current_persona_id is None:
        raise HTTPException(
            status_code=404, detail=f"No persona has been computed yet for master profile '{master_profile_id}'"
        )
    persona = db.get(CdpCustomerPersona, profile.current_persona_id)
    if persona is None:
        raise HTTPException(status_code=404, detail=f"CdpCustomerPersona '{profile.current_persona_id}' not found")
    return persona


@master_profiles_router.get("/{master_profile_id}/persona-history", response_model=list[PersonaHistoryRead])
@cache_response("master_profiles/persona_history", ttl=settings.cache_ttl_seconds)
def get_master_profile_persona_history(
    master_profile_id: uuid.UUID,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    """Audit trail of material persona changes for this profile, most-recent
    first (joins cdp_persona_history -> cdp_customer_personas by
    master_profile_id, since history rows only carry persona_id)."""
    if _master_crud.get(db, master_profile_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    stmt = (
        select(CdpPersonaHistory)
        .join(CdpCustomerPersona, CdpPersonaHistory.persona_id == CdpCustomerPersona.persona_id)
        .where(CdpCustomerPersona.master_profile_id == master_profile_id)
        .order_by(CdpPersonaHistory.changed_at.desc())
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
    try:
        validate_domain_value(db, payload.domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    obj = _master_crud.create(db, payload.model_dump())
    invalidate_prefix("master_profiles")
    return obj


@master_profiles_router.patch("/{master_profile_id}", response_model=MasterProfileRead)
def update_master_profile(master_profile_id: uuid.UUID, payload: MasterProfileUpdate, db: Session = Depends(get_db)):
    obj = _master_crud.get(db, master_profile_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    obj_in = payload.model_dump(exclude_unset=True)
    if "domain" in obj_in:
        try:
            validate_domain_value(db, obj_in.get("domain"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    obj = _master_crud.update(db, obj, obj_in)
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
    domain: Optional[str] = Query(default=None, pattern="^(retail|banking|healthcare|real_estate|travel|media|education)$"),
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
    domain: Optional[str] = Query(default=None, pattern="^(retail|banking|healthcare|real_estate|travel|media|education)$"),
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


# --- Customer Personas ("identity understanding", computed by backend-system/identity_resolution's
# PersonaResolutionEngine) -------------------------------------------------------

customer_personas_router = APIRouter(prefix="/customer-personas", tags=["Identity Resolution - Customer Personas"])
_persona_crud = CRUDBase(CdpCustomerPersona)


@customer_personas_router.get("/", response_model=list[CustomerPersonaRead])
@cache_response("customer_personas/list", ttl=settings.cache_ttl_seconds)
def list_customer_personas(
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None),
    master_profile_id: Optional[uuid.UUID] = None,
    persona_code: Optional[str] = None,
    is_active: Optional[bool] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    try:
        validate_domain_value(db, domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _persona_crud.list(
        db,
        skip=skip,
        limit=limit,
        tenant_id=tenant_id,
        domain=domain,
        master_profile_id=master_profile_id,
        persona_code=persona_code,
        is_active=is_active,
    )


@customer_personas_router.get("/analytics/summary", response_model=PersonaAnalyticsSummary)
@cache_response("customer_personas/analytics_summary", ttl=settings.cache_ttl_seconds)
def get_customer_persona_analytics_summary(
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None),
    is_active: Optional[bool] = None,
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    try:
        validate_domain_value(db, domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return identity_crud.persona_analytics_summary(
        db,
        tenant_id=tenant_id,
        domain=domain,
        is_active=is_active,
        days=days,
    )


@customer_personas_router.get("/{persona_id}", response_model=CustomerPersonaRead)
@cache_response("customer_personas/item", ttl=settings.cache_ttl_seconds)
def get_customer_persona(persona_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = _persona_crud.get(db, persona_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpCustomerPersona '{persona_id}' not found")
    return obj


@customer_personas_router.get("/{persona_id}/features", response_model=list[PersonaFeatureRead])
@cache_response("customer_personas/features", ttl=settings.cache_ttl_seconds)
def get_customer_persona_features(persona_id: uuid.UUID, db: Session = Depends(get_db)):
    if _persona_crud.get(db, persona_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpCustomerPersona '{persona_id}' not found")
    return _persona_feature_crud.list(db, skip=0, limit=settings.api_max_page_size, persona_id=persona_id)


@customer_personas_router.get("/{persona_id}/score-details", response_model=list[PersonaScoreDetailRead])
@cache_response("customer_personas/score_details", ttl=settings.cache_ttl_seconds)
def get_customer_persona_score_details(persona_id: uuid.UUID, db: Session = Depends(get_db)):
    if _persona_crud.get(db, persona_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpCustomerPersona '{persona_id}' not found")
    return _persona_score_detail_crud.list(db, skip=0, limit=settings.api_max_page_size, persona_id=persona_id)


@customer_personas_router.post("/", response_model=CustomerPersonaRead, status_code=201)
def create_customer_persona(payload: CustomerPersonaCreate, db: Session = Depends(get_db)):
    try:
        validate_domain_value(db, payload.domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    obj = _persona_crud.create(db, payload.model_dump())
    invalidate_prefix("customer_personas")
    return obj


@customer_personas_router.patch("/{persona_id}", response_model=CustomerPersonaRead)
def update_customer_persona(persona_id: uuid.UUID, payload: CustomerPersonaUpdate, db: Session = Depends(get_db)):
    obj = _persona_crud.get(db, persona_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpCustomerPersona '{persona_id}' not found")
    obj = _persona_crud.update(db, obj, payload.model_dump(exclude_unset=True))
    invalidate_prefix("customer_personas")
    return obj


@customer_personas_router.delete("/{persona_id}", status_code=204)
def delete_customer_persona(persona_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = _persona_crud.get(db, persona_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpCustomerPersona '{persona_id}' not found")
    _persona_crud.delete(db, obj)
    invalidate_prefix("customer_personas")


# --- Persona Features (explainability input signals; append-only) --------------

persona_features_router = APIRouter(prefix="/persona-features", tags=["Identity Resolution - Customer Personas"])
_persona_feature_crud = CRUDBase(CdpPersonaFeature)


@persona_features_router.get("/", response_model=list[PersonaFeatureRead])
@cache_response("persona_features/list", ttl=settings.cache_ttl_seconds)
def list_persona_features(
    persona_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    return _persona_feature_crud.list(db, skip=skip, limit=limit, persona_id=persona_id)


@persona_features_router.get("/{feature_id}", response_model=PersonaFeatureRead)
@cache_response("persona_features/item", ttl=settings.cache_ttl_seconds)
def get_persona_feature(feature_id: int, db: Session = Depends(get_db)):
    obj = _persona_feature_crud.get(db, feature_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpPersonaFeature '{feature_id}' not found")
    return obj


@persona_features_router.post("/", response_model=PersonaFeatureRead, status_code=201)
def create_persona_feature(payload: PersonaFeatureCreate, db: Session = Depends(get_db)):
    obj = _persona_feature_crud.create(db, payload.model_dump())
    invalidate_prefix("persona_features")
    return obj


# --- Persona Score Details (explainability score breakdown; append-only) -------

persona_score_details_router = APIRouter(
    prefix="/persona-score-details", tags=["Identity Resolution - Customer Personas"]
)
_persona_score_detail_crud = CRUDBase(CdpPersonaScoreDetail)


@persona_score_details_router.get("/", response_model=list[PersonaScoreDetailRead])
@cache_response("persona_score_details/list", ttl=settings.cache_ttl_seconds)
def list_persona_score_details(
    persona_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    return _persona_score_detail_crud.list(db, skip=skip, limit=limit, persona_id=persona_id)


@persona_score_details_router.get("/{score_id}", response_model=PersonaScoreDetailRead)
@cache_response("persona_score_details/item", ttl=settings.cache_ttl_seconds)
def get_persona_score_detail(score_id: int, db: Session = Depends(get_db)):
    obj = _persona_score_detail_crud.get(db, score_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpPersonaScoreDetail '{score_id}' not found")
    return obj


@persona_score_details_router.post("/", response_model=PersonaScoreDetailRead, status_code=201)
def create_persona_score_detail(payload: PersonaScoreDetailCreate, db: Session = Depends(get_db)):
    obj = _persona_score_detail_crud.create(db, payload.model_dump())
    invalidate_prefix("persona_score_details")
    return obj


# --- Persona History (audit trail of material persona changes; append-only) ----

persona_history_router = APIRouter(prefix="/persona-history", tags=["Identity Resolution - Customer Personas"])
_persona_history_crud = CRUDBase(CdpPersonaHistory)


@persona_history_router.get("/", response_model=list[PersonaHistoryRead])
@cache_response("persona_history/list", ttl=settings.cache_ttl_seconds)
def list_persona_history(
    persona_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    return _persona_history_crud.list(db, skip=skip, limit=limit, persona_id=persona_id)


@persona_history_router.get("/{history_id}", response_model=PersonaHistoryRead)
@cache_response("persona_history/item", ttl=settings.cache_ttl_seconds)
def get_persona_history_entry(history_id: int, db: Session = Depends(get_db)):
    obj = _persona_history_crud.get(db, history_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpPersonaHistory '{history_id}' not found")
    return obj


@persona_history_router.post("/", response_model=PersonaHistoryRead, status_code=201)
def create_persona_history_entry(payload: PersonaHistoryCreate, db: Session = Depends(get_db)):
    obj = _persona_history_crud.create(db, payload.model_dump())
    invalidate_prefix("persona_history")
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
    customer_personas_router,
    persona_features_router,
    persona_score_details_router,
    persona_history_router,
    resolution_status_router,
]
