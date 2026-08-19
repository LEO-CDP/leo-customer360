"""CIR-specific aggregate/reporting queries.

Per-row CRUD for cdp_master_profiles / cdp_raw_profiles_stage /
cdp_profile_links / cdp_profile_attributes is handled by the generic
CRUDBase (core/crud/base.py); this module only holds the aggregate queries
used by core/routers/reporting_api.py, mirroring the "Phân tích & Báo cáo"
section of core-customer360/identity-resolution.md.
"""

import uuid
from math import ceil
from typing import Optional
from core.utils.datetime import cutoff_for_days
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from core.models.identity import (
    CdpCustomerPersona,
    CdpDomainProfile,
    CdpMasterProfile,
    CdpPersonaArchetype,
    CdpProfileLink,
    CdpRawProfileStage,
)

STATUS_CODE_LABELS = {
    3: "processed",
    2: "in_progress",
    1: "new",
    0: "inactive",
    -1: "deleted",
}


def list_master_profiles_page(
    db: Session,
    *,
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = None,
    lifecycle_stage: Optional[str] = None,
    domain_attribute_key: Optional[str] = None,
    domain_attribute_value: Optional[str] = None,
    membership_tier: Optional[str] = None,
    clv_segment: Optional[str] = None,
    churn_risk_tier: Optional[str] = None,
    linked_raw_profile_count_min: Optional[int] = None,
    q: Optional[str] = None,
    days: Optional[int] = None,
    page: int = 1,
    page_size: int = 100,
) -> dict:
    """Returns a page of master profiles plus pagination metadata.

    This keeps filtering and pagination logic in the ORM layer so routers can
    stay thin and return a consistent envelope to clients.
    """
    where_clauses = []

    if tenant_id is not None:
        where_clauses.append(CdpMasterProfile.tenant_id == tenant_id)
    if domain is not None:
        where_clauses.append(CdpMasterProfile.domain == domain)
    if lifecycle_stage is not None:
        where_clauses.append(CdpMasterProfile.lifecycle_stage == lifecycle_stage)
    attr_key = domain_attribute_key
    attr_value = domain_attribute_value
    # Backward compatibility: membership_tier is now a domain attribute.
    if membership_tier is not None:
        attr_key = attr_key or "membership_tier"
        attr_value = attr_value or membership_tier
    if attr_key is not None and attr_value is not None:
        where_clauses.append(
            exists(
                select(1)
                .select_from(CdpDomainProfile)
                .where(
                    CdpDomainProfile.master_profile_id == CdpMasterProfile.master_profile_id,
                    CdpDomainProfile.tenant_id == CdpMasterProfile.tenant_id,
                    func.lower(CdpDomainProfile.domain_attributes[attr_key].astext) == attr_value.lower(),
                )
            )
        )
    if clv_segment is not None:
        where_clauses.append(CdpMasterProfile.clv_segment == clv_segment)
    if churn_risk_tier is not None:
        where_clauses.append(CdpMasterProfile.churn_risk_tier == churn_risk_tier)

    cutoff = cutoff_for_days(days)
    if cutoff is not None:
        where_clauses.append(CdpMasterProfile.created_at >= cutoff)

    if q:
        pattern = f"%{q}%"
        where_clauses.append(
            or_(
                CdpMasterProfile.full_name.ilike(pattern),
                CdpMasterProfile.persona_name.ilike(pattern),
                CdpMasterProfile.email.ilike(pattern),
                CdpMasterProfile.phone_number.ilike(pattern),
            )
        )

    page = max(1, page)
    page_size = max(1, page_size)
    offset = (page - 1) * page_size

    link_count_subq = (
        select(
            CdpProfileLink.tenant_id.label("tenant_id"),
            CdpProfileLink.master_profile_id.label("master_profile_id"),
            func.count().label("linked_raw_profile_count"),
        )
        .group_by(CdpProfileLink.tenant_id, CdpProfileLink.master_profile_id)
        .subquery()
    )

    linked_count_col = func.coalesce(link_count_subq.c.linked_raw_profile_count, 0)
    if linked_raw_profile_count_min is not None:
        where_clauses.append(linked_count_col >= linked_raw_profile_count_min)

    list_stmt = (
        select(CdpMasterProfile, linked_count_col.label("linked_raw_profile_count"))
        .outerjoin(
            link_count_subq,
            (link_count_subq.c.master_profile_id == CdpMasterProfile.master_profile_id)
            & (link_count_subq.c.tenant_id == CdpMasterProfile.tenant_id),
        )
        .where(*where_clauses)
        .order_by(CdpMasterProfile.last_activity_at.desc().nullslast(), CdpMasterProfile.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    count_stmt = (
        select(func.count())
        .select_from(CdpMasterProfile)
        .outerjoin(
            link_count_subq,
            (link_count_subq.c.master_profile_id == CdpMasterProfile.master_profile_id)
            & (link_count_subq.c.tenant_id == CdpMasterProfile.tenant_id),
        )
        .where(*where_clauses)
    )

    rows = db.execute(list_stmt).all()
    items = []
    for profile, linked_raw_profile_count in rows:
        profile.linked_raw_profile_count = int(linked_raw_profile_count or 0)
        items.append(profile)
    total = db.execute(count_stmt).scalar_one()
    total_pages = ceil(total / page_size) if total > 0 else 1

    return {
        "items": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        },
    }


def list_master_profiles_by_persona_category_page(
    db: Session,
    *,
    persona_category: str,
    tenant_id: Optional[uuid.UUID] = None,
    page: int = 1,
    page_size: int = 100,
) -> dict:
    """Page of master profiles whose current active persona match points at
    an archetype with the given persona_category -- backs the Persona
    Management "Total Matched Profiles" drill-down grouped by category
    (persona_category now lives on the SHARED cdp_persona_archetypes row,
    joined via cdp_customer_personas.persona_archetype_id)."""
    where_clauses = [
        CdpPersonaArchetype.persona_category == persona_category,
        CdpCustomerPersona.persona_archetype_id == CdpPersonaArchetype.persona_archetype_id,
        CdpCustomerPersona.is_active.is_(True),
        CdpCustomerPersona.master_profile_id == CdpMasterProfile.master_profile_id,
    ]
    if tenant_id is not None:
        where_clauses.append(CdpMasterProfile.tenant_id == tenant_id)

    page = max(1, page)
    page_size = max(1, page_size)
    offset = (page - 1) * page_size

    list_stmt = (
        select(CdpMasterProfile)
        .where(*where_clauses)
        .order_by(CdpMasterProfile.last_activity_at.desc().nullslast(), CdpMasterProfile.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    count_stmt = select(func.count()).select_from(CdpMasterProfile).where(*where_clauses)

    items = db.execute(list_stmt).scalars().all()
    total = db.execute(count_stmt).scalar_one()
    total_pages = ceil(total / page_size) if total > 0 else 1

    return {
        "items": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        },
    }


def list_master_profiles_by_persona_archetype_page(
    db: Session,
    *,
    persona_archetype_id: uuid.UUID,
    tenant_id: Optional[uuid.UUID] = None,
    page: int = 1,
    page_size: int = 100,
) -> dict:
    """Page of every master profile currently matched (is_active=TRUE) to a
    given persona archetype -- backs the Persona Management admin UI's
    per-archetype "Total Matched Profiles" drill-down."""
    where_clauses = [
        CdpCustomerPersona.persona_archetype_id == persona_archetype_id,
        CdpCustomerPersona.is_active.is_(True),
        CdpCustomerPersona.master_profile_id == CdpMasterProfile.master_profile_id,
    ]
    if tenant_id is not None:
        where_clauses.append(CdpMasterProfile.tenant_id == tenant_id)

    page = max(1, page)
    page_size = max(1, page_size)
    offset = (page - 1) * page_size

    list_stmt = (
        select(CdpMasterProfile)
        .where(*where_clauses)
        .order_by(CdpMasterProfile.last_activity_at.desc().nullslast(), CdpMasterProfile.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    count_stmt = select(func.count()).select_from(CdpMasterProfile).where(*where_clauses)

    items = db.execute(list_stmt).scalars().all()
    total = db.execute(count_stmt).scalar_one()
    total_pages = ceil(total / page_size) if total > 0 else 1

    return {
        "items": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        },
    }


def list_persona_archetypes_page(
    db: Session,
    *,
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = None,
    is_active: Optional[bool] = None,
    skip: int = 0,
    limit: int = 100,
) -> list[CdpPersonaArchetype]:
    """List shared persona archetypes (each carrying its own
    matched_profile_count) -- what the Persona Management admin UI must
    render, instead of raw per-profile cdp_customer_personas match rows."""
    stmt = select(CdpPersonaArchetype)
    if tenant_id is not None:
        stmt = stmt.where(CdpPersonaArchetype.tenant_id == tenant_id)
    if domain is not None:
        stmt = stmt.where(CdpPersonaArchetype.domain == domain)
    if is_active is not None:
        stmt = stmt.where(CdpPersonaArchetype.is_active == is_active)
    stmt = stmt.order_by(CdpPersonaArchetype.matched_profile_count.desc()).offset(skip).limit(limit)
    return list(db.execute(stmt).scalars().all())
