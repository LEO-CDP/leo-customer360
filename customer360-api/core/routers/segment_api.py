"""API for cdp_segments: segmentation/Audience Builder tag metadata, built via
the generic CRUD router factory (see core/routers/_generic.py) since it has a
simple single-column UUID primary key like the CRM entities. Also adds a
read-only "matched profiles" endpoint that actually executes a segment's
sql_rules against cdp_master_profiles (see core/utils/sql_safety.py for the
injection-safety validation applied before every execution).
"""

import logging
import re
import uuid
from collections.abc import Iterable
from typing import Any, Optional

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.cache import cache_response, invalidate_prefix
from core.config import settings
from core.crud.base import CRUDBase
from core.crud.segmentation import DOMAIN_ATTRIBUTES_JOIN_SQL
from core.database import get_db
from core.init_core_data import list_tenant_ids, seed_default_segments_with_breakdown
from core.models.segmentation import CdpSegment
from core.repositories.segment_respository import SegmentRepository
from core.routers._generic import build_crud_router, insert_before_item_routes
from core.schemas.identity import MasterProfileRead
from core.schemas.segmentation import SegmentCreate, SegmentRead, SegmentUpdate
from core.utils.dagster_client import DagsterJobTriggerError, dagster_client
from core.utils.domains import validate_domain_value
from core.utils.sql_safety import validate_sql_where_fragment

logger = logging.getLogger(__name__)

# Exposed for test mocking
_segment_crud = CRUDBase(CdpSegment)

# Segment rules (see get_segment_matched_profiles below) query cdp_master_profiles
# LEFT JOINed to cdp_domain_profiles (via DOMAIN_ATTRIBUTES_JOIN_SQL, aliased as
# "dp"), so the "field picker" endpoint below offers both plain cdp_master_profiles
# columns and cdp_domain_profiles.domain_attributes JSONB keys (as dp.domain_attributes->>'key').
_SEGMENTABLE_SOURCE_TABLES = ("cdp_master_profiles", "cdp_domain_profiles")

_RELATIVE_INTERVAL_PATTERN = re.compile(
    r"(?P<quote>['\"])(?P<sign>[+-])\s*(?P<amount>\d+)\s+"
    r"(?P<unit>milliseconds?|seconds?|minutes?|hours?|days?|weeks?|months?|years?)"
    r"(?P=quote)",
    re.IGNORECASE,
)


def _normalize_relative_intervals(sql_rules: str) -> str:
    """Translate UI date offsets into PostgreSQL interval expressions.

    The QueryBuilder sends datetime values such as ``'-5 days'`` as quoted
    strings. PostgreSQL cannot compare a timestamp to that text value, so
    convert it before both execution and audit SQL generation.
    """

    def replace(match: re.Match[str]) -> str:
        sign = "+" if match.group("sign") == "+" else "-"
        amount = match.group("amount")
        unit = match.group("unit").lower()
        return f"(now() {sign} INTERVAL '{amount} {unit}')"

    return _RELATIVE_INTERVAL_PATTERN.sub(replace, sql_rules)


def _has_wrapping_parentheses(value: str) -> bool:
    stripped = value.strip()
    if not stripped.startswith("(") or not stripped.endswith(")"):
        return False

    depth = 0
    quote: Optional[str] = None
    index = 0
    while index < len(stripped):
        char = stripped[index]
        if quote:
            if char == quote:
                if index + 1 < len(stripped) and stripped[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and index != len(stripped) - 1:
                return False
        index += 1
    return depth == 0 and quote is None


def _final_generated_sql(sql_rules: str, tenant_id: uuid.UUID) -> str:
    where_clause = sql_rules.strip() if _has_wrapping_parentheses(sql_rules) else f"({sql_rules.strip()})"
    return (
        f"SELECT master_profile_id FROM {settings.db_schema}.cdp_master_profiles "
        f"WHERE tenant_id = '{tenant_id}'::uuid AND {where_clause}"
    )


def _transform_segment_create(_db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    sql_rules = payload.get("sql_rules")
    if sql_rules:
        normalized_rules = _normalize_relative_intervals(sql_rules)
        payload["sql_rules"] = normalized_rules
        payload["final_generated_sql"] = _final_generated_sql(normalized_rules, payload["tenant_id"])
    return payload


def _transform_segment_update(_db: Session, segment: CdpSegment, payload: dict[str, Any]) -> dict[str, Any]:
    if "sql_rules" in payload:
        sql_rules = payload["sql_rules"]
        if sql_rules:
            normalized_rules = _normalize_relative_intervals(sql_rules)
            payload["sql_rules"] = normalized_rules
            payload["final_generated_sql"] = _final_generated_sql(normalized_rules, segment.tenant_id)
        else:
            payload["final_generated_sql"] = None
    return payload


def _trigger_segment_recompute(segment: CdpSegment, trigger_reason: str) -> None:
    """Best-effort submit of the tenant-scoped segmentation job after a
    segment row has been committed. A Dagster outage must not turn an already
    successful segment CRUD write into an HTTP failure; the scheduled job can
    reconcile the stale member_count later."""
    try:
        run_id = getattr(dagster_client.segmentation, trigger_reason)(
            tenant_id=str(segment.tenant_id),
            segment_id=str(segment.segment_id),
        )
    except DagsterJobTriggerError:
        logger.warning(
            "Could not submit segmentation_job for segment_id=%s (trigger_reason=%s); "
            "member_count will be refreshed by a later recompute.",
            segment.segment_id,
            trigger_reason,
        )
        return

    logger.info(
        "Submitted segmentation_job for segment_id=%s (tenant_id=%s, trigger_reason=%s, run_id=%s)",
        segment.segment_id,
        segment.tenant_id,
        trigger_reason,
        run_id,
    )


def _trigger_segment_recompute_after_create(segment: CdpSegment) -> None:
    _trigger_segment_recompute(segment, trigger_reason="create")


def _trigger_segment_recompute_after_update(segment: CdpSegment) -> None:
    _trigger_segment_recompute(segment, trigger_reason="update")


def _segment_integrity_error_detail(exc: IntegrityError) -> Optional[str]:
    """Returns a client-safe conflict message for known segment constraints."""
    original = getattr(exc, "orig", None)
    diagnostic = getattr(original, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == "uq_cdp_segments_tenant_tag":
        return "A segment with this tag already exists in this workspace."
    return None

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
    create_transform=_transform_segment_create,
    update_transform=_transform_segment_update,
    integrity_error_detail=_segment_integrity_error_detail,
    create_hook=_trigger_segment_recompute_after_create,
    update_hook=_trigger_segment_recompute_after_update,
)

PLATFORM_ADMIN_ROLES = {"platform_admin", "super_admin", "system_admin"}
TENANT_ADMIN_ROLES = PLATFORM_ADMIN_ROLES | {"tenant_admin", "admin"}


def _get_segment_or_404(repo: SegmentRepository, segment_id: uuid.UUID) -> CdpSegment:
    segment = repo.get_segment(segment_id)
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
    repo = SegmentRepository(db)
    segment = _get_segment_or_404(repo, segment_id)

    if not segment.sql_rules:
        return []

    where_fragment = _validated_where_fragment(segment.sql_rules)
    try:
        rows = repo.get_matched_profiles(segment_id, where_fragment, skip=skip, limit=limit)
        return [dict(row) for row in rows]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@segments_router.get("/{segment_id}/matched-profiles/count")
@cache_response("segments/matched_profiles_count", ttl=settings.cache_ttl_seconds)
def count_segment_matched_profiles(segment_id: uuid.UUID, db: Session = Depends(get_db)):
    """Same matching logic as ``get_segment_matched_profiles`` above, but
    returns just the total count (for pagination / summary display)."""
    repo = SegmentRepository(db)
    segment = _get_segment_or_404(repo, segment_id)

    if not segment.sql_rules:
        return {"count": 0}

    where_fragment = _validated_where_fragment(segment.sql_rules)
    try:
        count = repo.count_matched_profiles(segment_id, where_fragment)
        return {"count": count}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@segments_router.post("/{segment_id}/recompute")
def recompute_segment(segment_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    """Submits an asynchronous Dagster run for this tenant's one segment.

    The run updates ``member_count``/``last_computed_at`` and synchronizes
    ``segment_tag`` in the backend service. Use the status endpoint to track
    completion; the API does not perform the expensive profile scan inline.
    """
    caller_tenant_id = getattr(request.state, "tenant_id", None)
    if not caller_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="No tenant context found (missing X-Tenant-Id); recompute requires a tenant_id",
        )

    repo = SegmentRepository(db)
    segment = _get_segment_or_404(repo, segment_id)
    if str(segment.tenant_id) != str(caller_tenant_id):
        raise HTTPException(status_code=404, detail=f"CdpSegment '{segment_id}' not found")
    if not segment.sql_rules:
        raise HTTPException(status_code=400, detail="Segment has no sql_rules to compute")
    _validated_where_fragment(segment.sql_rules)

    try:
        run_id = dagster_client.segmentation.refresh(
            tenant_id=str(caller_tenant_id),
            segment_id=str(segment_id),
        )
    except DagsterJobTriggerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "status": "submitted",
        "run_id": run_id,
        "tenant_id": str(caller_tenant_id),
        "segment_id": str(segment_id),
        "job_name": settings.dagster_segmentation_job_name,
        "message": (
            "Segment recompute job submitted to Dagster; membership updates asynchronously. "
            "Poll /segments/admin/recompute-status/{run_id} for completion."
        ),
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
        # matched-profiles endpoints and the stored member_count, so their
        # cached responses are now stale.
        invalidate_prefix("cdp_segments")
        invalidate_prefix("segments/matched_profiles")
        invalidate_prefix("segments/matched_profiles_count")

    return result


@segments_router.get("/segmentable-profile-attributes")
@cache_response("segments/segmentable_profile_attributes", ttl=settings.cache_ttl_seconds)
def get_segmentable_profile_attributes(
    domain: Optional[str] = Query(default=None),
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

    ``domain`` (optional) additionally filters to attributes with
    ``domain_scope IN ('all', <domain>)``, matching ``cdp_master_profiles``/
    ``cdp_segments``'s own ``domain`` column. Validated against active
    ``sys_domain`` codes rather than a hardcoded list.
    """
    if domain:
        try:
            validate_domain_value(db, domain)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    repo = SegmentRepository(db)
    return repo.get_segmentable_attributes(domain=domain)


# Static sub-path added after build_crud_router(); must be reordered ahead of
# the generic "/{item_id}" routes or it'd be shadowed by them (see
# insert_before_item_routes docstring).
insert_before_item_routes(segments_router)

all_segment_routers = [segments_router]
