"""Customer Identity Resolution (CIR) reporting/analytics API.

Mirrors the ad-hoc SQL queries in the "Phân tích & Báo cáo" section of
core-customer360/identity-resolution.md as proper JSON endpoints.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.cache import cache_response
from core.database import get_db
from core.repositories.reporting_respository import ReportingRepository
from core.schemas.reporting import CirSummary, DuplicateMasterProfile, IdentityGraphCoverage

router = APIRouter(prefix="/reporting", tags=["Identity Resolution - Reporting"])


@router.get("/summary", response_model=CirSummary)
@cache_response("reporting/summary", ttl=300)
def get_cir_summary(
    tenant_id: Optional[uuid.UUID] = None,
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """One-shot overview: raw/master profile totals, processing funnel,
    domain/source breakdowns, and duplicate (merged) master profile count."""
    repo = ReportingRepository(db)
    return repo.get_cir_summary(tenant_id=tenant_id, days=days)


@router.get("/master-profiles/duplicates", response_model=list[DuplicateMasterProfile])
@cache_response("reporting/duplicates", ttl=300)
def get_duplicate_master_profiles(
    tenant_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = Query(default=20, le=100),
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Master profiles that consolidated 2+ raw profiles -- i.e. identity
    resolution actually merged records from different source systems."""
    repo = ReportingRepository(db)
    return repo.list_duplicate_master_profiles(tenant_id=tenant_id, skip=skip, limit=limit, days=days)


@router.get("/identity-graph/coverage", response_model=IdentityGraphCoverage)
@cache_response("reporting/coverage", ttl=300)
def get_identity_graph_coverage(
    tenant_id: Optional[uuid.UUID] = None,
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Adoption of each identity channel (email/phone/device/advertising/cookie/
    external id/national id) across all resolved master profiles."""
    repo = ReportingRepository(db)
    return repo.get_identity_graph_coverage(tenant_id=tenant_id, days=days)
