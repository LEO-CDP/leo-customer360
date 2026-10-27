"""Customer Identity Resolution (CIR) reporting/analytics API.

Mirrors the ad-hoc SQL queries in the "Phân tích & Báo cáo" section of
core-customer360/identity-resolution.md as proper JSON endpoints.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from core.auth import require_tenant
from core.cache import cache_response
from core.database import get_db
from core.repositories.reporting_repository import ReportingRepository
from leo_customer360_dao.schemas.reporting import (
    CirSummary,
    DuplicateMasterProfile,
    IdentityGraphCoverage,
    JourneyReportResponse,
)

router = APIRouter(prefix="/reporting", tags=["Identity Resolution - Reporting"])


def _resolve_reporting_tenant(request: Request, tenant_id: Optional[uuid.UUID]) -> uuid.UUID:
    """Resolve and enforce the authenticated tenant for reporting queries.

    The query parameter is retained for backwards compatibility, but it can
    never override the tenant established by authentication. This also keeps
    cache keys tenant-specific because each cached route receives ``Request``.
    """
    resolved = uuid.UUID(require_tenant(request))
    if tenant_id is not None and tenant_id != resolved:
        raise HTTPException(status_code=403, detail="Tenant does not match the authenticated tenant")
    return resolved


@router.get("/journey", response_model=JourneyReportResponse)
@cache_response("reporting/journey", ttl=300)
def get_journey_report(
    request: Request,
    tenant_id: Optional[uuid.UUID] = None,
    days: int = Query(default=90, ge=1, le=365),
    window_days: int = Query(default=30, alias="window"),
    db: Session = Depends(get_db),
):
    """Return seven customer-journey stages with KPI, campaign, and agent data."""
    effective_tenant = _resolve_reporting_tenant(request, tenant_id)
    repo = ReportingRepository(db)
    try:
        return repo.get_journey_report(
            tenant_id=effective_tenant,
            days=days,
            window_days=window_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/summary", response_model=CirSummary)
@cache_response("reporting/summary", ttl=300)
def get_cir_summary(
    request: Request,
    tenant_id: Optional[uuid.UUID] = None,
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """One-shot overview: raw/master profile totals, processing funnel,
    domain/source breakdowns, and duplicate (merged) master profile count."""
    effective_tenant = _resolve_reporting_tenant(request, tenant_id)
    repo = ReportingRepository(db)
    return repo.get_cir_summary(tenant_id=effective_tenant, days=days)


@router.get("/master-profiles/duplicates", response_model=list[DuplicateMasterProfile])
@cache_response("reporting/duplicates", ttl=300)
def get_duplicate_master_profiles(
    request: Request,
    tenant_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = Query(default=20, le=100),
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Master profiles that consolidated 2+ raw profiles -- i.e. identity
    resolution actually merged records from different source systems."""
    effective_tenant = _resolve_reporting_tenant(request, tenant_id)
    repo = ReportingRepository(db)
    return repo.list_duplicate_master_profiles(
        tenant_id=effective_tenant,
        skip=skip,
        limit=limit,
        days=days,
    )


@router.get("/identity-graph/coverage", response_model=IdentityGraphCoverage)
@cache_response("reporting/coverage", ttl=300)
def get_identity_graph_coverage(
    request: Request,
    tenant_id: Optional[uuid.UUID] = None,
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Adoption of each identity channel (email/phone/device/advertising/cookie/
    external id/national id) across all resolved master profiles."""
    effective_tenant = _resolve_reporting_tenant(request, tenant_id)
    repo = ReportingRepository(db)
    return repo.get_identity_graph_coverage(tenant_id=effective_tenant, days=days)
