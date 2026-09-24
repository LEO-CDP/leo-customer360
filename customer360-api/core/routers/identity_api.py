"""Routers for the Customer Identity Resolution (CIR) core models: master
profiles, raw profile staging, profile links, and the matching-rule
metadata / throttle-status tables consumed by customer360-backend/identity_resolution.
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.cache import cache_response, invalidate_prefix
from core.repositories.identity_repository import IdentityRepository
from leo_customer360_dao.config import settings
from leo_customer360_dao.crud import identity as identity_crud
from leo_customer360_dao.crud import profile360 as profile360_crud
from leo_customer360_dao.crud.base import CRUDBase
from core.database import get_db
from leo_customer360_dao.models.identity import (
    CdpCustomerPersona,
    CdpDomainProfile,
    CdpIdentityIndex,
    CdpIdResolutionStatus,
    CdpMasterProfile,
    CdpPersonaHistory,
    CdpProfileAttribute,
    CdpProfileLink,
    CdpProfileMergeHistory,
    CdpRawProfileStage,
)
from leo_customer360_dao.models.system import SysDomain
from core.routers._generic import build_crud_router
from leo_customer360_dao.schemas.identity import (
    DomainAttributeUpsert,
    DomainProfileCreate,
    DomainProfileRead,
    DomainProfileUpdate,
    IdentityIndexCreate,
    IdentityIndexRead,
    IdentityIndexUpdate,
    IdResolutionStatusRead,
    LinkedRawProfileDetailRead,
    MasterProfileCreate,
    MasterProfileListResponse,
    MasterProfileRead,
    MasterProfileUpdate,
    PersonaAnalyticsSummary,
    PersonaHistoryRead,
    CustomerPersonaRead,
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
from leo_customer360_dao.schemas.profile360 import ChannelActivity, EngagementSummary, TimelineEntry, TopInterest
from leo_customer360_dao.repositories.event_query_repository import (
    EventDataSourceError,
    EventQueryError,
)
from leo_customer360_dao.repositories.master_profile_event_repository import (
    MasterProfileEventStoreError,
)
from core.utils.domains import validate_domain_value

# --- Master Profiles ---------------------------------------------------------

master_profiles_router = APIRouter(prefix="/master-profiles", tags=["Identity Resolution - Master Profiles"])
_master_crud = CRUDBase(CdpMasterProfile)


def _identity_repository(db: Session) -> IdentityRepository:
    """Build an identity repository from the current router test seams."""
    return IdentityRepository(
        db,
        link_crud=_link_crud,
        master_crud=_master_crud,
        raw_crud=_raw_crud,
        merge_history_crud=_merge_history_crud,
        identity_module=identity_crud,
        profile360_module=profile360_crud,
    )


@master_profiles_router.get("/", response_model=MasterProfileListResponse)
@cache_response("master_profiles/list", ttl=settings.cache_ttl_seconds)
def list_master_profiles(
    tenant_id: Optional[uuid.UUID] = None,
    data_source_id: Optional[uuid.UUID] = Query(default=None),
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
    return _identity_repository(db).list_master_profiles(
        tenant_id=tenant_id,
        data_source_id=data_source_id,
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
    return {"count": _identity_repository(db).count_master_profiles(tenant_id=tenant_id, domain=domain)}


@master_profiles_router.get("/{master_profile_id}", response_model=MasterProfileRead)
@cache_response("master_profiles/item", ttl=settings.cache_ttl_seconds)
def get_master_profile(master_profile_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = _identity_repository(db).get_master_profile(master_profile_id)
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
    return _identity_repository(db).list_master_profile_links(master_profile_id, limit)


@master_profiles_router.get("/{master_profile_id}/domain-profiles", response_model=list[DomainProfileRead])
@cache_response("master_profiles/domain_profiles", ttl=settings.cache_ttl_seconds)
def get_master_profile_domain_profiles(master_profile_id: uuid.UUID, db: Session = Depends(get_db)):
    """Every cdp_domain_profiles row for this master profile (one per business
    domain the person has activity in, e.g. banking + retail), each carrying
    its own domain_attributes JSONB bag."""
    repository = _identity_repository(db)
    if repository.get_master_profile(master_profile_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    return repository.get_domain_profiles(master_profile_id)


@master_profiles_router.post("/{master_profile_id}/domain-attributes", response_model=DomainProfileRead, status_code=201)
def upsert_master_profile_domain_attribute(
    master_profile_id: uuid.UUID, payload: DomainAttributeUpsert, db: Session = Depends(get_db)
):
    """Adds/overwrites one ``domain_attributes`` key for this profile in the
    given ``domain``, creating the ``cdp_domain_profiles`` row for that
    (master_profile_id, domain) pair if it doesn't exist yet. Merges into the
    existing JSONB (never replaces the whole bag), so this is a safe way for
    the UI/API to "add a new attribute" without needing to resend every
    existing key. The write fires customer360.sync_domain_attribute_catalog()
    (see database-schema.sql), which auto-registers a brand-new attribute_key
    into cdp_profile_attributes if one doesn't already exist there."""
    repository = _identity_repository(db)
    master = repository.get_master_profile(master_profile_id)
    if master is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    try:
        validate_domain_value(db, payload.domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    domain_profile = repository.upsert_domain_attribute(
        master, payload.domain, payload.attribute_key, payload.attribute_value
    )
    if domain_profile is None:
        raise HTTPException(status_code=422, detail=f"Unknown domain '{payload.domain}'")
    invalidate_prefix("master_profiles/domain_profiles")
    invalidate_prefix("profile_attributes")
    return domain_profile


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
    repository = _identity_repository(db)
    master_profile = repository.get_master_profile(master_profile_id)
    if master_profile is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")

    row = repository.get_linked_raw_profile(master_profile_id, raw_profile_id, master_profile.tenant_id)
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
    the resolved identity by customer360-backend/identity_resolution's
    PersonaResolutionEngine), resolved via current_persona_id. 404 if the
    profile has no persona computed yet."""
    repository = _identity_repository(db)
    profile = repository.get_master_profile(master_profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    if profile.current_persona_id is None:
        raise HTTPException(
            status_code=404, detail=f"No persona has been computed yet for master profile '{master_profile_id}'"
        )
    persona = repository.get_persona(profile)
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
    repository = _identity_repository(db)
    if repository.get_master_profile(master_profile_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    return repository.get_persona_history(master_profile_id, limit)


@master_profiles_router.get("/{master_profile_id}/engagement-summary", response_model=EngagementSummary)
@cache_response("master_profiles/engagement_summary", ttl=settings.cache_ttl_seconds)
def get_master_profile_engagement_summary(
    master_profile_id: uuid.UUID, days: int = Query(default=90, ge=1, le=365), db: Session = Depends(get_db)
):
    """Login/transaction counts, spend, and last-interaction timestamp for the
    last ``days`` days. Behavioral event metrics are being migrated to the S3
    Silver query path; CRM transactions and contacts remain PostgreSQL-backed."""
    repository = _identity_repository(db)
    if repository.get_master_profile(master_profile_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    return repository.get_engagement_summary(master_profile_id, days)


@master_profiles_router.get("/{master_profile_id}/channel-activity", response_model=ChannelActivity)
@cache_response("master_profiles/channel_activity", ttl=settings.cache_ttl_seconds)
def get_master_profile_channel_activity(
    master_profile_id: uuid.UUID, days: int = Query(default=90, ge=1, le=365), db: Session = Depends(get_db)
):
    """Cross-channel activity counts (app/web sessions, customer service
    contacts, transactions) for the last ``days`` days."""
    repository = _identity_repository(db)
    if repository.get_master_profile(master_profile_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    return repository.get_channel_activity(master_profile_id, days)


@master_profiles_router.get("/{master_profile_id}/top-interests", response_model=list[TopInterest])
@cache_response("master_profiles/top_interests", ttl=settings.cache_ttl_seconds)
def get_master_profile_top_interests(
    master_profile_id: uuid.UUID, limit: int = Query(default=5, ge=1, le=20), db: Session = Depends(get_db)
):
    """Top behavioral-event categories for this profile from the event
    projection. The projection is being migrated from PostgreSQL raw-event
    storage to the S3 Silver query path."""
    repository = _identity_repository(db)
    if repository.get_master_profile(master_profile_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    return repository.get_top_interests(master_profile_id, limit)


@master_profiles_router.get("/{master_profile_id}/timeline", response_model=list[TimelineEntry])
@cache_response("master_profiles/timeline", ttl=settings.cache_ttl_seconds)
def get_master_profile_timeline(
    request: Request,
    master_profile_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    data_source_id: Optional[uuid.UUID] = Query(default=None),
    from_event_time: Optional[datetime] = Query(default=None),
    to_event_time: Optional[datetime] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Unified, most-recent-first activity feed merging behavioral events,
    transactions, and logged customer service contacts. When ``data_source_id``
    is supplied, the feed contains only behavioral events from that active,
    tenant-owned source. The range is order-independent and defaults to the
    last seven days."""
    repository = _identity_repository(db)
    if repository.get_master_profile(master_profile_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    try:
        return repository.get_timeline(
            master_profile_id,
            limit=limit,
            data_source_id=data_source_id,
            from_event_time=from_event_time,
            to_event_time=to_event_time,
        )
    except EventDataSourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EventQueryError as exc:
        raise HTTPException(status_code=503, detail="Event lake query failed") from exc
    except MasterProfileEventStoreError as exc:
        raise HTTPException(status_code=503, detail="Master profile event projection unavailable") from exc


@master_profiles_router.post("/", response_model=MasterProfileRead, status_code=201)
def create_master_profile(payload: MasterProfileCreate, db: Session = Depends(get_db)):
    try:
        validate_domain_value(db, payload.domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    obj = _identity_repository(db).create_master_profile(payload.model_dump())
    invalidate_prefix("master_profiles")
    return obj


@master_profiles_router.patch("/{master_profile_id}", response_model=MasterProfileRead)
def update_master_profile(master_profile_id: uuid.UUID, payload: MasterProfileUpdate, db: Session = Depends(get_db)):
    repository = _identity_repository(db)
    obj = repository.get_master_profile(master_profile_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    obj_in = payload.model_dump(exclude_unset=True)
    if "domain" in obj_in:
        try:
            validate_domain_value(db, obj_in.get("domain"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    obj = repository.update_master_profile(obj, obj_in)
    invalidate_prefix("master_profiles")
    return obj


@master_profiles_router.delete("/{master_profile_id}", status_code=204)
def delete_master_profile(master_profile_id: uuid.UUID, db: Session = Depends(get_db)):
    repository = _identity_repository(db)
    obj = repository.get_master_profile(master_profile_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpMasterProfile '{master_profile_id}' not found")
    repository.delete_master_profile(obj)
    invalidate_prefix("master_profiles")


# --- Raw Profiles Stage -------------------------------------------------------

raw_profiles_router = APIRouter(prefix="/raw-profiles", tags=["Identity Resolution - Raw Profiles"])
_raw_crud = CRUDBase(CdpRawProfileStage)


@raw_profiles_router.get("/", response_model=list[RawProfileRead])
@cache_response("raw_profiles/list", ttl=settings.cache_ttl_seconds)
def list_raw_profiles(
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None),
    source_system: Optional[str] = None,
    status_code: Optional[int] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    # DB-backed validation (sys_domain) instead of a hardcoded regex, matching
    # every other domain-filtered endpoint (master-profiles, personas, content).
    try:
        validate_domain_value(db, domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _identity_repository(db).list_raw_profiles(
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
    domain: Optional[str] = Query(default=None),
    source_system: Optional[str] = None,
    status_code: Optional[int] = None,
    db: Session = Depends(get_db),
):
    try:
        validate_domain_value(db, domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "count": _identity_repository(db).count_raw_profiles(
            tenant_id=tenant_id, domain=domain, source_system=source_system, status_code=status_code
        )
    }


@raw_profiles_router.get("/{raw_profile_id}", response_model=RawProfileRead)
@cache_response("raw_profiles/item", ttl=settings.cache_ttl_seconds)
def get_raw_profile(raw_profile_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = _identity_repository(db).get_raw_profile(raw_profile_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpRawProfileStage '{raw_profile_id}' not found")
    return obj


@raw_profiles_router.post("/", response_model=RawProfileRead, status_code=201)
def create_raw_profile(payload: RawProfileCreate, db: Session = Depends(get_db)):
    """Ingests a raw profile event (status_code defaults to 1 = new/unprocessed,
    ready to be picked up by customer360-backend/identity_resolution)."""
    obj = _identity_repository(db).create_raw_profile(payload.model_dump())
    invalidate_prefix("raw_profiles")
    return obj


@raw_profiles_router.patch("/{raw_profile_id}", response_model=RawProfileRead)
def update_raw_profile(raw_profile_id: uuid.UUID, payload: RawProfileUpdate, db: Session = Depends(get_db)):
    repository = _identity_repository(db)
    obj = repository.get_raw_profile(raw_profile_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpRawProfileStage '{raw_profile_id}' not found")
    obj = repository.update_raw_profile(obj, payload.model_dump(exclude_unset=True))
    invalidate_prefix("raw_profiles")
    return obj


@raw_profiles_router.delete("/{raw_profile_id}", status_code=204)
def delete_raw_profile(raw_profile_id: uuid.UUID, db: Session = Depends(get_db)):
    repository = _identity_repository(db)
    obj = repository.get_raw_profile(raw_profile_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpRawProfileStage '{raw_profile_id}' not found")
    repository.delete_raw_profile(obj)
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
    return _identity_repository(db).list_profile_links(
        skip=skip,
        limit=limit,
        tenant_id=tenant_id,
        raw_profile_id=raw_profile_id,
        master_profile_id=master_profile_id,
    )


@profile_links_router.get("/{link_id}", response_model=ProfileLinkRead)
@cache_response("profile_links/item", ttl=settings.cache_ttl_seconds)
def get_profile_link(link_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = _identity_repository(db).get_profile_link(link_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpProfileLink '{link_id}' not found")
    return obj


@profile_links_router.post("/", response_model=ProfileLinkRead, status_code=201)
def create_profile_link(payload: ProfileLinkCreate, db: Session = Depends(get_db)):
    obj = _identity_repository(db).create_profile_link(payload.model_dump())
    invalidate_prefix("profile_links")
    return obj


@profile_links_router.delete("/{link_id}", status_code=204)
def delete_profile_link(link_id: uuid.UUID, db: Session = Depends(get_db)):
    repository = _identity_repository(db)
    obj = repository.get_profile_link(link_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpProfileLink '{link_id}' not found")
    repository.delete_profile_link(obj)
    invalidate_prefix("profile_links")


# --- Domain Profiles (per-domain persona/engagement/domain_attributes) ----------

domain_profiles_router = build_crud_router(
    model=CdpDomainProfile,
    pk_field="domain_profile_id",
    pk_type=uuid.UUID,
    create_schema=DomainProfileCreate,
    update_schema=DomainProfileUpdate,
    read_schema=DomainProfileRead,
    prefix="/domain-profiles",
    tags=["Identity Resolution - Domain Profiles"],
)


# --- Profile Attributes (matching-rule metadata) --------------------------------

profile_attributes_router = build_crud_router(
    model=CdpProfileAttribute,
    pk_field="id",
    pk_type=uuid.UUID,
    create_schema=ProfileAttributeCreate,
    update_schema=ProfileAttributeUpdate,
    read_schema=ProfileAttributeRead,
    prefix="/profile-attributes",
    tags=["Identity Resolution - Matching Rules"],
    create_validator=lambda db, payload: validate_domain_value(
        db, payload.get("domain_scope"), field_name="domain_scope", allow_all=True
    ),
    update_validator=lambda db, obj, payload: validate_domain_value(
        db, payload.get("domain_scope"), field_name="domain_scope", allow_all=True
    ),
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
    return _identity_repository(db).list_profile_merge_history(
        skip=skip,
        limit=limit,
        tenant_id=tenant_id,
        target_master_profile_id=target_master_profile_id,
        source_master_profile_id=source_master_profile_id,
    )


@profile_merge_history_router.get("/{merge_id}", response_model=ProfileMergeHistoryRead)
@cache_response("profile_merge_history/item", ttl=settings.cache_ttl_seconds)
def get_profile_merge_history(merge_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = _identity_repository(db).get_profile_merge_history(merge_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpProfileMergeHistory '{merge_id}' not found")
    return obj


@profile_merge_history_router.post("/", response_model=ProfileMergeHistoryRead, status_code=201)
def create_profile_merge_history(payload: ProfileMergeHistoryCreate, db: Session = Depends(get_db)):
    obj = _identity_repository(db).create_profile_merge_history(payload.model_dump())
    invalidate_prefix("profile_merge_history")
    return obj


# --- Resolution status (real-time throttle state) -------------------------------

resolution_status_router = APIRouter(prefix="/resolution-status", tags=["Identity Resolution - Matching Rules"])


@resolution_status_router.get("/", response_model=IdResolutionStatusRead)
def get_resolution_status(db: Session = Depends(get_db)):
    obj = _identity_repository(db).get_resolution_status()
    if obj is None:
        raise HTTPException(
            status_code=404,
            detail="cdp_id_resolution_status has not been initialized yet "
            "(run customer360-backend/identity_resolution/scripts/init_sample_data.py).",
        )
    return obj


all_identity_routers = [
    master_profiles_router,
    raw_profiles_router,
    profile_links_router,
    domain_profiles_router,
    profile_attributes_router,
    identity_index_router,
    profile_merge_history_router,
    resolution_status_router,
]
