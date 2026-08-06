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
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from core.cache import cache_response, invalidate_prefix
from core.config import settings
from core.crud.base import CRUDBase
from core.crud.segmentation import DOMAIN_ATTRIBUTES_JOIN_SQL, recompute_segment_membership
from core.database import get_db
from core.init_core_data import list_tenant_ids, seed_default_segments_with_breakdown
from core.models.identity import CdpProfileAttribute
from core.models.segmentation import CdpSegment
from core.routers._generic import build_crud_router
from core.schemas.identity import MasterProfileRead
from core.schemas.segmentation import SegmentCreate, SegmentRead, SegmentUpdate
from core.utils.dagster_client import DagsterJobTriggerError, dagster_client
from core.utils.domains import validate_domain_value
from core.utils.sql_safety import validate_sql_where_fragment

# Segment rules (see get_segment_matched_profiles below) query cdp_master_profiles
# LEFT JOINed to cdp_domain_profiles (via DOMAIN_ATTRIBUTES_JOIN_SQL, aliased as
# "dp"), so the "field picker" endpoint below offers both plain cdp_master_profiles
# columns and cdp_domain_profiles.domain_attributes JSONB keys (as dp.domain_attributes->>'key').
_SEGMENTABLE_SOURCE_TABLES = ("cdp_master_profiles", "cdp_domain_profiles")
_DOMAIN_SCOPE_PATTERN = r"^(all|retail|banking|real_estate|travel|media|education)$"

segments_router = build_crud_router(
    model=CdpSegment,
    pk_field="segment_id",
    pk_type=uuid.UUID,
    create_schema=SegmentCreate,
    update_schema=SegmentUpdate,
    read_schema=SegmentRead,
    prefix="/segments",
    tags=["Segmentation"],
    create_validator=lambda db, payload: validate_domain_value(db, payload.get("domain"), allow_all=True),
    update_validator=lambda db, payload: validate_domain_value(db, payload.get("domain"), allow_all=True),
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


def _enforce_recompute_all_permissions(request: Request) -> None:
    """``POST /segments/admin/recompute-all`` only ever recomputes the
    caller's own tenant (see ``recompute_all_segments`` below), so this is a
    tenant-scoped action, not a platform-wide one -- gate it the same way as
    the single-tenant branches of default-segment seeding (tenant admin
    role required, platform admin implicitly allowed)."""
    if not settings.sso_login:
        return

    payload = getattr(request.state, "user", None)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="Authentication required")

    if not _is_tenant_admin(payload):
        raise HTTPException(status_code=403, detail="Tenant admin role required to trigger segment recompute")


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
        {DOMAIN_ATTRIBUTES_JOIN_SQL.format(schema=settings.db_schema)}
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
        {DOMAIN_ATTRIBUTES_JOIN_SQL.format(schema=settings.db_schema)}
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


@segments_router.post("/admin/recompute-all")
def recompute_all_segments(request: Request):
    """Triggers an async, out-of-process recompute of every active segment
    belonging to the CALLER'S OWN TENANT (from the ``X-Tenant-Id`` header /
    ``request.state.tenant_id``) and returns immediately. This NEVER
    recomputes other tenants' segments -- there is no way to pass a
    different tenant_id to this endpoint.

    This does NOT run the recompute inline: ``cdp_master_profiles`` can hold
    1M+ rows in production, and scanning it once per active segment inside
    an HTTP request handler would block an API worker for the whole scan and
    risk request timeouts. Instead this submits a run of
    ``backend-system/segmentation``'s ``segmentation_job``, scoped to the
    caller's tenant via run_config, to the Dagster webserver (see
    core/utils/dagster_client.py) -- Dagster's daemon/run worker executes it
    out-of-process, with its own retry policy, and the caller polls
    ``GET /segments/admin/recompute-status/{run_id}`` for completion instead
    of waiting on this call.
    """
    caller_tenant_id = getattr(request.state, "tenant_id", None)
    if not caller_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="No tenant context found (missing X-Tenant-Id); refresh requires a tenant_id",
        )

    _enforce_recompute_all_permissions(request)

    try:
        run_id = dagster_client.segmentation.refresh(tenant_id=str(caller_tenant_id))
    except DagsterJobTriggerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "status": "submitted",
        "run_id": run_id,
        "tenant_id": str(caller_tenant_id),
        "job_name": settings.dagster_segmentation_job_name,
        "message": (
            "Segment recompute job submitted to Dagster for this tenant; "
            "membership updates asynchronously. Poll "
            "/segments/admin/recompute-status/{run_id} for completion."
        ),
    }


@segments_router.get("/admin/recompute-status/{run_id}")
def get_recompute_job_status(run_id: str):
    """Polls the status of a ``segmentation_job`` run previously submitted
    via ``POST /segments/admin/recompute-all``. ``status`` is one of
    ``running`` / ``success`` / ``failure`` (collapsed from Dagster's more
    granular ``raw_status``, e.g. QUEUED/STARTED/SUCCESS/FAILURE).

    Also includes ``start_time``/``end_time`` (ISO 8601, ``None`` until the
    run reaches that point), ``duration_seconds`` (``None`` until finished),
    and ``steps_succeeded``/``steps_failed`` counts -- enough detail for the
    frontend to show e.g. "Failed -- 2 of 5 steps failed, ran for 42s"
    instead of a bare "failure" (see
    ``core.utils.dagster_client.DagsterService.get_status``).

    Cache invalidation for the matched-profiles endpoints only happens once
    the run actually reaches ``success`` (called out here since the caller,
    not this endpoint, decides when to stop polling and refresh its own
    segment list).
    """
    try:
        result = dagster_client.segmentation.get_status(run_id)
    except DagsterJobTriggerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if result["status"] == "success":
        # Recomputed membership/tags can change the result of the read-only
        # matched-profiles endpoints, so their cached responses are now stale.
        invalidate_prefix("segments/matched_profiles")
        invalidate_prefix("segments/matched_profiles_count")

    return result


def _segmentable_field(attribute: CdpProfileAttribute) -> str:
    """SQL-safe field reference for the Audience Builder field picker: a bare
    cdp_master_profiles column, or the dp.domain_attributes->>'key' JSONB path
    (see DOMAIN_ATTRIBUTES_JOIN_SQL) for cdp_domain_profiles-sourced attributes."""
    if getattr(attribute, "source_table", None) == "cdp_domain_profiles":
        return f"dp.domain_attributes->>'{attribute.attribute_internal_code}'"
    return attribute.master_profile_column or attribute.attribute_internal_code


@segments_router.get("/segmentable-profile-attributes")
@cache_response("segments/segmentable_profile_attributes", ttl=settings.cache_ttl_seconds)
def get_segmentable_profile_attributes(
    domain: Optional[str] = Query(default=None, pattern=_DOMAIN_SCOPE_PATTERN),
    db: Session = Depends(get_db),
):
    """Returns the catalog of attributes that are valid to reference in a
    segment's ``sql_rules`` (Audience Builder field picker), sourced from
    ``cdp_profile_attributes`` -- the same metadata-driven catalog that also
    configures CIR matching rules (see module docstring). This is a
    read-only endpoint that does not require authentication, since it only
    returns metadata about the system and not any sensitive data.

    Only rows with ``is_segmentable = true``, ``status = 'ACTIVE'`` and
    ``source_table IN ('cdp_master_profiles', 'cdp_domain_profiles')`` are
    returned. ``cdp_master_profiles`` rows return their bare column name as
    ``field``; ``cdp_domain_profiles`` rows (JSONB keys in
    ``domain_attributes``, e.g. ``risk_segment``/``membership_tier``) return
    ``dp.domain_attributes->>'<key>'`` -- the alias exposed by
    ``DOMAIN_ATTRIBUTES_JOIN_SQL``, which every ``sql_rules`` execution site
    (``get_segment_matched_profiles``/``count_segment_matched_profiles``/
    ``recompute_segment_membership``) LEFT JOINs in. CIR-only matching keys
    that live on ``cdp_raw_profiles_stage`` (e.g. the raw ``device_id``/
    ``cookie_id`` staging columns) are never valid fields for a segment
    rule.

    ``domain`` (optional, one of ``retail``/``banking``/``real_estate``/
    ``travel``/``media``/``education``) additionally filters to attributes with
    ``domain_scope IN ('all', <domain>)``, matching ``cdp_master_profiles``/
    ``cdp_segments``'s own ``domain`` column.
    """
    stmt = select(CdpProfileAttribute).where(
        CdpProfileAttribute.is_segmentable.is_(True),
        CdpProfileAttribute.status == "ACTIVE",
        CdpProfileAttribute.source_table.in_(_SEGMENTABLE_SOURCE_TABLES),
    )
    if domain:
        stmt = stmt.where(CdpProfileAttribute.domain_scope.in_(["all", domain]))
    stmt = stmt.order_by(CdpProfileAttribute.attribute_group, CdpProfileAttribute.display_order)

    attributes = db.execute(stmt).scalars().all()
    return [
        {
            "field": _segmentable_field(attribute),
            "name": attribute.name,
            "description": attribute.description,
            "attribute_group": attribute.attribute_group,
            "data_type": attribute.data_type,
            "domain_scope": attribute.domain_scope,
            "is_pii": attribute.is_pii,
        }
        for attribute in attributes
    ]


# The generic CRUD router's `GET /{item_id}` (registered above, inside
# build_crud_router()) uses an untyped path template ("/segments/{item_id}",
# with the uuid.UUID conversion happening in the handler signature, not the
# path itself) -- so it fully matches ANY single-segment GET path, including
# the literal "/segmentable-profile-attributes" route just defined. Since
# Starlette dispatches to the first fully-matching route in registration
# order, that earlier route would otherwise shadow this one (returning a 422
# "invalid UUID" instead of ever calling get_segmentable_profile_attributes).
# Move this route's just-appended APIRoute ahead of the generic "/{item_id}"
# GET route to fix that.
_segmentable_attrs_route = segments_router.routes.pop()
_item_id_get_index = next(
    i
    for i, route in enumerate(segments_router.routes)
    if getattr(route, "path", None) == "/segments/{item_id}" and "GET" in getattr(route, "methods", set())
)
segments_router.routes.insert(_item_id_get_index, _segmentable_attrs_route)

all_segment_routers = [segments_router]
