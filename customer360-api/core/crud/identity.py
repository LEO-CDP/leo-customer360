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

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from core.models.identity import CdpMasterProfile, CdpProfileLink, CdpRawProfileStage

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

    list_stmt = (
        select(CdpMasterProfile)
        .where(*where_clauses)
        .order_by(CdpMasterProfile.last_activity_at.desc().nullslast(), CdpMasterProfile.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    count_stmt = select(func.count()).select_from(CdpMasterProfile).where(*where_clauses)

    items = list(db.execute(list_stmt).scalars().all())
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
        "with_national_id": _count(CdpMasterProfile.national_id.isnot(None)),
    }
