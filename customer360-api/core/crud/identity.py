"""CIR-specific aggregate/reporting queries.

Per-row CRUD for cdp_master_profiles / cdp_raw_profiles_stage /
cdp_profile_links / cdp_profile_attributes is handled by the generic
CRUDBase (core/crud/base.py); this module only holds the aggregate queries
used by core/routers/reporting.py, mirroring the "Phân tích & Báo cáo"
section of core-customer360/identity-resolution.md.
"""

import uuid
from math import ceil
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from core.models.identity import CdpCustomerPersona, CdpDomainProfile, CdpMasterProfile, CdpProfileLink, CdpRawProfileStage

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

    cutoff = _cutoff_for_days(days)
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


def _cutoff_for_days(days: Optional[int]) -> Optional[datetime]:
    if days is None:
        return None
    return datetime.utcnow() - timedelta(days=days)


def _filter_recent(stmt, model, days: Optional[int]):
    cutoff = _cutoff_for_days(days)
    if cutoff is not None:
        stmt = stmt.where(model.created_at >= cutoff)
    return stmt


def _filter_tenant(stmt, model, tenant_id: Optional[uuid.UUID]):
    if tenant_id is not None:
        stmt = stmt.where(model.tenant_id == tenant_id)
    return stmt


def count_raw_profiles(db: Session, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None) -> int:
    stmt = select(func.count()).select_from(CdpRawProfileStage)
    stmt = _filter_tenant(stmt, CdpRawProfileStage, tenant_id)
    stmt = _filter_recent(stmt, CdpRawProfileStage, days)
    return db.execute(stmt).scalar_one()


def count_master_profiles(db: Session, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None) -> int:
    stmt = select(func.count()).select_from(CdpMasterProfile)
    stmt = _filter_tenant(stmt, CdpMasterProfile, tenant_id)
    stmt = _filter_recent(stmt, CdpMasterProfile, days)
    return db.execute(stmt).scalar_one()


def raw_profiles_by_status(db: Session, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None) -> list[dict]:
    stmt = select(CdpRawProfileStage.status_code, func.count().label("count")).group_by(
        CdpRawProfileStage.status_code
    )
    stmt = _filter_tenant(stmt, CdpRawProfileStage, tenant_id)
    stmt = _filter_recent(stmt, CdpRawProfileStage, days)
    rows = db.execute(stmt).all()
    return [
        {"status_code": code, "label": STATUS_CODE_LABELS.get(code, "unknown"), "count": count}
        for code, count in rows
    ]


def raw_profiles_by_domain(db: Session, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None) -> list[dict]:
    stmt = select(CdpRawProfileStage.domain, func.count().label("count")).group_by(CdpRawProfileStage.domain)
    stmt = _filter_tenant(stmt, CdpRawProfileStage, tenant_id)
    stmt = _filter_recent(stmt, CdpRawProfileStage, days)
    return [{"domain": domain, "count": count} for domain, count in db.execute(stmt).all()]


def master_profiles_by_domain(db: Session, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None) -> list[dict]:
    stmt = select(CdpMasterProfile.domain, func.count().label("count")).group_by(CdpMasterProfile.domain)
    stmt = _filter_tenant(stmt, CdpMasterProfile, tenant_id)
    stmt = _filter_recent(stmt, CdpMasterProfile, days)
    return [{"domain": domain, "count": count} for domain, count in db.execute(stmt).all()]


def raw_profiles_by_source_system(db: Session, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None) -> list[dict]:
    stmt = select(
        CdpRawProfileStage.source_system, CdpRawProfileStage.domain, func.count().label("count")
    ).group_by(CdpRawProfileStage.source_system, CdpRawProfileStage.domain)
    stmt = _filter_tenant(stmt, CdpRawProfileStage, tenant_id)
    stmt = _filter_recent(stmt, CdpRawProfileStage, days)
    return [{"source_system": s, "domain": d, "count": c} for s, d, c in db.execute(stmt).all()]


def count_duplicate_master_profiles(db: Session, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None) -> int:
    """Counts master profiles linked to 2+ raw profiles (i.e. identity
    resolution actually merged multiple source records together)."""
    link_counts = select(CdpProfileLink.master_profile_id, func.count().label("link_count")).group_by(
        CdpProfileLink.master_profile_id
    )
    link_counts = _filter_tenant(link_counts, CdpProfileLink, tenant_id)
    link_counts = _filter_recent(link_counts, CdpProfileLink, days)
    subq = link_counts.subquery()
    stmt = select(func.count()).select_from(subq).where(subq.c.link_count > 1)
    return db.execute(stmt).scalar_one()


def list_duplicate_master_profiles(
    db: Session,
    tenant_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = 100,
    days: Optional[int] = None,
) -> list[dict]:
    """Lists master profiles that consolidated 2+ raw profiles, most-merged first."""
    link_count_subq = (
        select(CdpProfileLink.master_profile_id, func.count().label("link_count"))
        .group_by(CdpProfileLink.master_profile_id)
        .subquery()
    )
    stmt = (
        select(
            CdpMasterProfile.master_profile_id,
            CdpMasterProfile.domain,
            CdpMasterProfile.full_name,
            CdpMasterProfile.is_hashed,
            CdpMasterProfile.persona_name,
            CdpMasterProfile.source_systems,
            link_count_subq.c.link_count,
        )
        .join(link_count_subq, link_count_subq.c.master_profile_id == CdpMasterProfile.master_profile_id)
        .where(link_count_subq.c.link_count > 1)
        .order_by(link_count_subq.c.link_count.desc())
        .offset(skip)
        .limit(limit)
    )
    stmt = _filter_tenant(stmt, CdpMasterProfile, tenant_id)
    stmt = _filter_recent(stmt, CdpMasterProfile, days)
    rows = db.execute(stmt).all()
    return [
        {
            "master_profile_id": row.master_profile_id,
            "domain": row.domain,
            "full_name": row.full_name,
            "is_hashed": row.is_hashed,
            "persona_name": row.persona_name,
            "linked_raw_profile_count": row.link_count,
            "source_systems": row.source_systems,
        }
        for row in rows
    ]


def identity_graph_coverage(db: Session, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None) -> dict:
    """Counts how many master profiles have each identity channel populated."""
    total = count_master_profiles(db, tenant_id, days=days)

    def _count(condition) -> int:
        stmt = select(func.count()).select_from(CdpMasterProfile).where(condition)
        stmt = _filter_tenant(stmt, CdpMasterProfile, tenant_id)
        stmt = _filter_recent(stmt, CdpMasterProfile, days)
        return db.execute(stmt).scalar_one()

    return {
        "total_master_profiles": total,
        "with_email": _count(CdpMasterProfile.email.isnot(None)),
        "with_phone_number": _count(CdpMasterProfile.phone_number.isnot(None)),
        "with_device_id": _count(func.cardinality(CdpMasterProfile.device_ids) > 0),
        "with_advertising_id": _count(func.cardinality(CdpMasterProfile.advertising_ids) > 0),
        "with_cookie_id": _count(func.cardinality(CdpMasterProfile.cookie_ids) > 0),
        "with_external_id": _count(CdpMasterProfile.external_ids != {}),
        "with_national_id": _count(
            exists(
                select(1)
                .select_from(CdpDomainProfile)
                .where(
                    CdpDomainProfile.master_profile_id == CdpMasterProfile.master_profile_id,
                    CdpDomainProfile.tenant_id == CdpMasterProfile.tenant_id,
                    CdpDomainProfile.domain_attributes["national_id"].astext.isnot(None),
                    CdpDomainProfile.domain_attributes["national_id"].astext != "",
                )
            )
        ),
    }


def persona_analytics_summary(
    db: Session,
    *,
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = None,
    is_active: Optional[bool] = None,
    days: Optional[int] = None,
) -> dict:
    """Aggregate analytics for customer personas used by Persona Management UI."""

    where_clauses = []
    if tenant_id is not None:
        where_clauses.append(CdpCustomerPersona.tenant_id == tenant_id)
    if domain is not None:
        where_clauses.append(CdpCustomerPersona.domain == domain)
    if is_active is not None:
        where_clauses.append(CdpCustomerPersona.is_active == is_active)

    cutoff = _cutoff_for_days(days)
    if cutoff is not None:
        where_clauses.append(CdpCustomerPersona.computed_at >= cutoff)

    base = select(CdpCustomerPersona).where(*where_clauses).subquery()

    total_personas = db.execute(select(func.count()).select_from(base)).scalar_one()
    active_personas = db.execute(
        select(func.count()).select_from(base).where(base.c.is_active.is_(True))
    ).scalar_one()
    inactive_personas = max(0, total_personas - active_personas)
    unique_master_profiles = db.execute(
        select(func.count(func.distinct(base.c.master_profile_id))).select_from(base)
    ).scalar_one()

    avg_persona_score_raw, avg_confidence_score_raw = db.execute(
        select(func.avg(base.c.persona_score), func.avg(base.c.confidence_score)).select_from(base)
    ).one()

    def _bucket_rows(column_name: str) -> list[dict]:
        col = getattr(base.c, column_name)
        rows = db.execute(
            select(col, func.count().label("count"))
            .select_from(base)
            .group_by(col)
            .order_by(func.count().desc())
        ).all()
        return [{"value": (value or "unknown"), "count": count} for value, count in rows]

    return {
        "total_personas": total_personas,
        "active_personas": active_personas,
        "inactive_personas": inactive_personas,
        "unique_master_profiles": unique_master_profiles,
        "avg_persona_score": round(float(avg_persona_score_raw or 0), 2),
        "avg_confidence_score": round(float(avg_confidence_score_raw or 0), 4),
        "by_domain": _bucket_rows("domain"),
        "by_category": _bucket_rows("persona_category"),
        "by_risk_level": _bucket_rows("risk_level"),
        "by_value_tier": _bucket_rows("customer_value_tier"),
    }
