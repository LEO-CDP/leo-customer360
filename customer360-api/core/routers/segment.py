"""API for cdp_segments: segmentation/Audience Builder tag metadata, built via
the generic CRUD router factory (see core/routers/_generic.py) since it has a
simple single-column UUID primary key like the CRM entities. Also adds a
read-only "matched profiles" endpoint that actually executes a segment's
sql_rules against cdp_master_profiles (see core/utils/sql_safety.py for the
injection-safety validation applied before every execution).
"""

import uuid
from collections.abc import Iterable
from typing import Any, Optional

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.cache import cache_response, invalidate_prefix
from core.config import settings
from core.crud.base import CRUDBase
from core.crud.segmentation import recompute_segment_membership
from core.database import get_db
from core.init_core_data import list_tenant_ids, seed_default_segments_with_breakdown
from core.models.segmentation import CdpSegment
from core.routers._generic import build_crud_router
from core.schemas.identity import MasterProfileRead
from core.schemas.segmentation import SegmentCreate, SegmentRead, SegmentUpdate
from core.utils.sql_safety import validate_sql_where_fragment

segments_router = build_crud_router(
    model=CdpSegment,
    pk_field="segment_id",
    pk_type=uuid.UUID,
    create_schema=SegmentCreate,
    update_schema=SegmentUpdate,
    read_schema=SegmentRead,
    prefix="/segments",
    tags=["Segmentation"],
)

_segment_crud = CRUDBase(CdpSegment)

PLATFORM_ADMIN_ROLES = {"platform_admin", "super_admin", "system_admin"}
TENANT_ADMIN_ROLES = PLATFORM_ADMIN_ROLES | {"tenant_admin", "admin"}


def _get_segment_or_404(db: Session, segment_id: uuid.UUID) -> CdpSegment:
    segment = _segment_crud.get(db, segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail=f"CdpSegment '{segment_id}' not found")
    return segment


def _validated_where_fragment(sql_rules: str) -> str:
    """Re-validates sql_rules immediately before execution (defense-in-depth
    against rows written outside the API's own Pydantic validation, e.g.
    core/init_core_data.py's direct ORM inserts) and turns a failure into a
    clean 400 instead of an unhandled 500."""
    try:
        return validate_sql_where_fragment(sql_rules)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _extract_roles_from_payload(payload: dict[str, Any]) -> set[str]:
    roles: set[str] = set()

    def _extend(items: Iterable[Any]) -> None:
        for item in items:
            if isinstance(item, str) and item.strip():
                roles.add(item.strip().lower())

    _extend(payload.get("roles") or [])
    realm_access = payload.get("realm_access")
    if isinstance(realm_access, dict):
        _extend(realm_access.get("roles") or [])

    resource_access = payload.get("resource_access")
    if isinstance(resource_access, dict):
        for resource_info in resource_access.values():
            if isinstance(resource_info, dict):
                _extend(resource_info.get("roles") or [])
    return roles


def _is_platform_admin(payload: dict[str, Any]) -> bool:
    if payload.get("is_platform_admin") is True:
        return True
    return bool(_extract_roles_from_payload(payload) & PLATFORM_ADMIN_ROLES)


def _is_tenant_admin(payload: dict[str, Any]) -> bool:
    if _is_platform_admin(payload):
        return True
    return bool(_extract_roles_from_payload(payload) & TENANT_ADMIN_ROLES)


def _resolve_seed_target_tenants(db: Session, tenant_id: Optional[uuid.UUID], all_tenants: bool) -> list[uuid.UUID]:
    if all_tenants:
        return list_tenant_ids(db)
    if tenant_id is not None:
        return [tenant_id]
    return []


def _enforce_seed_permissions(request: Request, tenant_id: Optional[uuid.UUID], all_tenants: bool) -> None:
    # Local dev mode is intentionally flexible for easier setup/testing.
    if not settings.sso_login:
        return

    payload = getattr(request.state, "user", None)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="Authentication required")

    caller_tenant_id = getattr(request.state, "tenant_id", None)
    is_platform_admin = _is_platform_admin(payload)
    is_tenant_admin = _is_tenant_admin(payload)

    if all_tenants and not is_platform_admin:
        raise HTTPException(status_code=403, detail="Platform admin role required for all-tenants segment seeding")

    if tenant_id is None and all_tenants:
        return

    if tenant_id is None:
        if caller_tenant_id is None:
            raise HTTPException(status_code=400, detail="No tenant context found; pass tenant_id explicitly")
        if not is_tenant_admin:
            raise HTTPException(status_code=403, detail="Tenant admin role required to seed default segments")
        return

    if caller_tenant_id is None:
        if not is_platform_admin:
            raise HTTPException(status_code=403, detail="Platform admin role required to seed another tenant")
        return

    if str(tenant_id) == str(caller_tenant_id):
        if not is_tenant_admin:
            raise HTTPException(status_code=403, detail="Tenant admin role required to seed default segments")
        return

    if not is_platform_admin:
        raise HTTPException(status_code=403, detail="Platform admin role required to seed another tenant")


@segments_router.get("/{segment_id}/matched-profiles", response_model=list[MasterProfileRead])
@cache_response("segments/matched_profiles", ttl=settings.cache_ttl_seconds)
def get_segment_matched_profiles(
    segment_id: uuid.UUID,
    skip: int = 0,
    limit: int = Query(default=50, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    """Runs the segment's ``sql_rules`` (validated as a safe WHERE-clause
    fragment) against ``cdp_master_profiles``, scoped to the segment's own
    tenant, and returns the currently-matching active profiles."""
    segment = _get_segment_or_404(db, segment_id)
    if not segment.sql_rules:
        return []

    where_fragment = _validated_where_fragment(segment.sql_rules)
    stmt = text(
        f"""
        SELECT * FROM {settings.db_schema}.cdp_master_profiles
        WHERE tenant_id = :tenant_id AND status_code = 1 AND ({where_fragment})
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :skip
        """
    )
    rows = db.execute(stmt, {"tenant_id": str(segment.tenant_id), "limit": limit, "skip": skip}).mappings().all()
    return [dict(row) for row in rows]


@segments_router.get("/{segment_id}/matched-profiles/count")
@cache_response("segments/matched_profiles_count", ttl=settings.cache_ttl_seconds)
def count_segment_matched_profiles(segment_id: uuid.UUID, db: Session = Depends(get_db)):
    """Same matching logic as ``get_segment_matched_profiles`` above, but
    returns just the total count (for pagination / summary display)."""
    segment = _get_segment_or_404(db, segment_id)
    if not segment.sql_rules:
        return {"count": 0}

    where_fragment = _validated_where_fragment(segment.sql_rules)
    stmt = text(
        f"""
        SELECT count(*) FROM {settings.db_schema}.cdp_master_profiles
        WHERE tenant_id = :tenant_id AND status_code = 1 AND ({where_fragment})
        """
    )
    count = db.execute(stmt, {"tenant_id": str(segment.tenant_id)}).scalar_one()
    return {"count": count}


@segments_router.post("/{segment_id}/recompute")
def recompute_segment(segment_id: uuid.UUID, db: Session = Depends(get_db)):
    """Re-runs the segment's ``sql_rules`` against ``cdp_master_profiles``
    (see core.crud.segmentation.recompute_segment_membership), updating
    ``member_count``/``last_computed_at`` and syncing ``segment_tag`` into/out
    of ``cdp_master_profiles.segmentation_tags`` for matching/non-matching
    profiles."""
    segment = _get_segment_or_404(db, segment_id)
    if not segment.sql_rules:
        raise HTTPException(status_code=400, detail="Segment has no sql_rules to compute")

    try:
        recompute_segment_membership(db, segment)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Recomputed membership/tags can change the result of the read-only
    # matched-profiles endpoints, so their cached responses are now stale.
    invalidate_prefix("segments/matched_profiles")
    invalidate_prefix("segments/matched_profiles_count")

    return {
        "segment_id": str(segment.segment_id),
        "member_count": segment.member_count,
        "last_computed_at": segment.last_computed_at,
    }


@segments_router.post("/admin/defaults/seed")
def seed_segment_defaults_for_tenants(
    request: Request,
    tenant_id: Optional[uuid.UUID] = None,
    all_tenants: bool = False,
    db: Session = Depends(get_db),
):
    """Admin endpoint to seed/backfill system default segments.

    - ``tenant_id`` omitted + ``all_tenants=false``: seed caller tenant.
    - ``tenant_id`` provided: seed one explicit tenant.
    - ``all_tenants=true``: seed all tenants (platform admin only).
    """
    if tenant_id is not None and all_tenants:
        raise HTTPException(status_code=400, detail="Pass either tenant_id or all_tenants=true, not both")

    _enforce_seed_permissions(request, tenant_id=tenant_id, all_tenants=all_tenants)

    if all_tenants:
        target_tenant_ids = _resolve_seed_target_tenants(db, tenant_id=tenant_id, all_tenants=True)
    elif tenant_id is not None:
        target_tenant_ids = [tenant_id]
    else:
        caller_tenant_id = getattr(request.state, "tenant_id", None)
        if caller_tenant_id is None:
            raise HTTPException(status_code=400, detail="No tenant context found; pass tenant_id explicitly")
        target_tenant_ids = [uuid.UUID(str(caller_tenant_id))]

    inserted, inserted_by_tenant = seed_default_segments_with_breakdown(db, tenant_ids=target_tenant_ids)
    results = [
        {
            "tenant_id": str(tid),
            "inserted": count,
        }
        for tid, count in inserted_by_tenant.items()
    ]

    return {
        "requested_all_tenants": all_tenants,
        "seeded_tenants": len(target_tenant_ids),
        "inserted_segments": inserted,
        "results": results,
    }


all_segment_routers = [segments_router]
