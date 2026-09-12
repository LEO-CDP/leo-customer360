"""Admin router for the segment-ID driven CRM sync engine.

Exposes ``POST /api/v1/admin/crm/sync-segment/{segment_id}`` -- recompute one
segment and route its members into the ``crm_*`` tables by lifecycle stage (see
``core.crud.crm_sync``) -- plus read-only audit endpoints over
``crm_segment_sync_runs`` for the "audit evidence" the flow requires.

Every route is tenant-scoped: the segment (and every write) is bound to the
caller's own ``request.state.tenant_id`` (resolved by ``core.auth`` and enforced
again by Row-Level Security), and gated behind a tenant-admin role when SSO is on.
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import require_tenant, require_tenant_admin
from core.crud.crm_sync import sync_segment_to_crm
from core.database import get_db
from core.models.crm import SegmentSyncRun
from core.repositories.segment_respository import SegmentRepository
from core.schemas.crm import SegmentCrmSyncResponse, SegmentSyncRunRead

logger = logging.getLogger(__name__)

crm_sync_router = APIRouter(prefix="/admin/crm", tags=["CRM - Segment Sync"])


def _require_tenant(request: Request) -> str:
    # Delegate to the shared gate (validates + normalizes the tenant UUID).
    return require_tenant(request)


def _enforce_sync_permissions(request: Request) -> None:
    require_tenant_admin(request, "segment sync")


@crm_sync_router.post("/sync-segment/{segment_id}", response_model=SegmentCrmSyncResponse)
def sync_segment_crm(
    segment_id: uuid.UUID,
    request: Request,
    dry_run: bool = Query(False, description="Resolve + route members and return counts only, writing nothing."),
    db: Session = Depends(get_db),
):
    """Recompute one segment, then route + upsert its members into the crm_*
    tables (customer -> crm_customer_contacts + crm_transactions; lead ->
    crm_lead + crm_lead_source; otherwise -> crm_contact). Idempotent across
    retries/replays; every run is audited in crm_segment_sync_runs.

    ``dry_run=true`` returns the per-route counts without writing any target
    facts or recomputing membership.
    """
    caller_tenant_id = _require_tenant(request)
    _enforce_sync_permissions(request)

    segment = SegmentRepository(db).get_segment(segment_id)
    if segment is None or str(segment.tenant_id) != caller_tenant_id:
        raise HTTPException(status_code=404, detail=f"CdpSegment '{segment_id}' not found")
    if not segment.sql_rules:
        raise HTTPException(status_code=400, detail="Segment has no sql_rules to sync")

    try:
        return sync_segment_to_crm(
            db,
            segment,
            tenant_id=caller_tenant_id,
            triggered_by=getattr(request.state, "user_id", None),
            dry_run=dry_run,
        )
    except ValueError as exc:
        # Unsafe/absent sql_rules surfaced by the engine's defense-in-depth check.
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@crm_sync_router.get("/sync-runs", response_model=list[SegmentSyncRunRead])
def list_segment_sync_runs(
    request: Request,
    segment_id: Optional[uuid.UUID] = Query(None, description="Filter to one segment's runs."),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Most-recent CRM sync runs for the caller's tenant (audit evidence)."""
    caller_tenant_id = _require_tenant(request)
    _enforce_sync_permissions(request)

    stmt = select(SegmentSyncRun).where(SegmentSyncRun.tenant_id == uuid.UUID(caller_tenant_id))
    if segment_id is not None:
        stmt = stmt.where(SegmentSyncRun.segment_id == segment_id)
    stmt = stmt.order_by(SegmentSyncRun.started_at.desc()).limit(limit)
    return db.execute(stmt).scalars().all()


@crm_sync_router.get("/sync-runs/{sync_run_id}", response_model=SegmentSyncRunRead)
def get_segment_sync_run(sync_run_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    """One CRM sync run by id, scoped to the caller's tenant."""
    caller_tenant_id = _require_tenant(request)
    _enforce_sync_permissions(request)

    run = db.get(SegmentSyncRun, sync_run_id)
    if run is None or str(run.tenant_id) != caller_tenant_id:
        raise HTTPException(status_code=404, detail=f"SegmentSyncRun '{sync_run_id}' not found")
    return run


all_crm_sync_routers = [crm_sync_router]
