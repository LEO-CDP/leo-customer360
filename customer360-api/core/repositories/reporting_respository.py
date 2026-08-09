"""Customer Identity Resolution (CIR) reporting/analytics repository.

Encapsulates analytics queries for CIR reporting endpoints:
- CIR summary overview (totals, processing funnel, breakdowns)
- Duplicate master profiles (identity resolution merge results)
- Identity graph coverage (channel adoption metrics)

Uses the same synchronous SQLAlchemy Session as the rest of the API
(see core/database.py).
"""

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from core.crud import identity as identity_crud
from core.schemas.reporting import CirSummary, DuplicateMasterProfile, IdentityGraphCoverage


class ReportingRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_cir_summary(
        self, tenant_id: Optional[uuid.UUID] = None, days: int = 90
    ) -> CirSummary:
        """One-shot overview: raw/master profile totals, processing funnel,
        domain/source breakdowns, and duplicate (merged) master profile count."""
        by_status = identity_crud.raw_profiles_by_status(self.session, tenant_id, days=days)
        status_counts = {row["status_code"]: row["count"] for row in by_status}

        return CirSummary(
            total_raw_profiles=identity_crud.count_raw_profiles(self.session, tenant_id, days=days),
            total_master_profiles=identity_crud.count_master_profiles(self.session, tenant_id, days=days),
            processed_raw_profiles=status_counts.get(3, 0),
            pending_raw_profiles=status_counts.get(1, 0),
            in_progress_raw_profiles=status_counts.get(2, 0),
            duplicate_master_profile_count=identity_crud.count_duplicate_master_profiles(
                self.session, tenant_id, days=days
            ),
            raw_profiles_by_status=by_status,
            raw_profiles_by_domain=identity_crud.raw_profiles_by_domain(self.session, tenant_id, days=days),
            master_profiles_by_domain=identity_crud.master_profiles_by_domain(self.session, tenant_id, days=days),
            raw_profiles_by_source_system=identity_crud.raw_profiles_by_source_system(
                self.session, tenant_id, days=days
            ),
        )

    def list_duplicate_master_profiles(
        self,
        tenant_id: Optional[uuid.UUID] = None,
        skip: int = 0,
        limit: int = 20,
        days: int = 90,
    ) -> list[DuplicateMasterProfile]:
        """Master profiles that consolidated 2+ raw profiles -- i.e. identity
        resolution actually merged records from different source systems."""
        return identity_crud.list_duplicate_master_profiles(
            self.session, tenant_id, skip=skip, limit=limit, days=days
        )

    def get_identity_graph_coverage(
        self, tenant_id: Optional[uuid.UUID] = None, days: int = 90
    ) -> IdentityGraphCoverage:
        """Adoption of each identity channel (email/phone/device/advertising/cookie/
        external id/national id) across all resolved master profiles."""
        return identity_crud.identity_graph_coverage(self.session, tenant_id, days=days)
