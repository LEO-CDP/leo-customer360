"""Customer Identity Resolution (CIR) reporting/analytics repository.

Encapsulates analytics queries for CIR reporting endpoints:
- CIR summary overview (totals, processing funnel, breakdowns)
- Duplicate master profiles (identity resolution merge results)
- Identity graph coverage (channel adoption metrics)
- Persona analytics summary for persona management dashboards

Uses the same synchronous SQLAlchemy Session as the rest of the API
(see core/database.py).
"""

import uuid
from datetime import datetime, timedelta
from math import ceil
from typing import Optional

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
from core.schemas.reporting import CirSummary, DuplicateMasterProfile, IdentityGraphCoverage

STATUS_CODE_LABELS = {
    3: "processed",
    2: "in_progress",
    1: "new",
    0: "inactive",
    -1: "deleted",
}


class ReportingRepository:
    def __init__(self, session: Session):
        self.session = session

    def _cutoff_for_days(self, days: Optional[int]) -> Optional[datetime]:
        if days is None:
            return None
        return datetime.utcnow() - timedelta(days=days)

    def _filter_recent(self, stmt, model, days: Optional[int]):
        cutoff = self._cutoff_for_days(days)
        if cutoff is not None:
            stmt = stmt.where(model.created_at >= cutoff)
        return stmt

    def _filter_tenant(self, stmt, model, tenant_id: Optional[uuid.UUID]):
        if tenant_id is not None:
            stmt = stmt.where(model.tenant_id == tenant_id)
        return stmt

    def count_raw_profiles(
        self, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None
    ) -> int:
        stmt = select(func.count()).select_from(CdpRawProfileStage)
        stmt = self._filter_tenant(stmt, CdpRawProfileStage, tenant_id)
        stmt = self._filter_recent(stmt, CdpRawProfileStage, days)
        return self.session.execute(stmt).scalar_one()

    def count_master_profiles(
        self, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None
    ) -> int:
        stmt = select(func.count()).select_from(CdpMasterProfile)
        stmt = self._filter_tenant(stmt, CdpMasterProfile, tenant_id)
        stmt = self._filter_recent(stmt, CdpMasterProfile, days)
        return self.session.execute(stmt).scalar_one()

    def raw_profiles_by_status(
        self, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None
    ) -> list[dict]:
        stmt = select(CdpRawProfileStage.status_code, func.count().label("count")).group_by(
            CdpRawProfileStage.status_code
        )
        stmt = self._filter_tenant(stmt, CdpRawProfileStage, tenant_id)
        stmt = self._filter_recent(stmt, CdpRawProfileStage, days)
        rows = self.session.execute(stmt).all()
        return [
            {"status_code": code, "label": STATUS_CODE_LABELS.get(code, "unknown"), "count": count}
            for code, count in rows
        ]

    def raw_profiles_by_domain(
        self, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None
    ) -> list[dict]:
        stmt = select(CdpRawProfileStage.domain, func.count().label("count")).group_by(CdpRawProfileStage.domain)
        stmt = self._filter_tenant(stmt, CdpRawProfileStage, tenant_id)
        stmt = self._filter_recent(stmt, CdpRawProfileStage, days)
        return [{"domain": domain, "count": count} for domain, count in self.session.execute(stmt).all()]

    def master_profiles_by_domain(
        self, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None
    ) -> list[dict]:
        stmt = select(CdpMasterProfile.domain, func.count().label("count")).group_by(CdpMasterProfile.domain)
        stmt = self._filter_tenant(stmt, CdpMasterProfile, tenant_id)
        stmt = self._filter_recent(stmt, CdpMasterProfile, days)
        return [{"domain": domain, "count": count} for domain, count in self.session.execute(stmt).all()]

    def raw_profiles_by_source_system(
        self, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None
    ) -> list[dict]:
        stmt = select(
            CdpRawProfileStage.source_system, CdpRawProfileStage.domain, func.count().label("count")
        ).group_by(CdpRawProfileStage.source_system, CdpRawProfileStage.domain)
        stmt = self._filter_tenant(stmt, CdpRawProfileStage, tenant_id)
        stmt = self._filter_recent(stmt, CdpRawProfileStage, days)
        return [{"source_system": s, "domain": d, "count": c} for s, d, c in self.session.execute(stmt).all()]

    def count_duplicate_master_profiles(
        self, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None
    ) -> int:
        """Counts master profiles linked to 2+ raw profiles."""
        link_counts = select(CdpProfileLink.master_profile_id, func.count().label("link_count")).group_by(
            CdpProfileLink.master_profile_id
        )
        link_counts = self._filter_tenant(link_counts, CdpProfileLink, tenant_id)
        link_counts = self._filter_recent(link_counts, CdpProfileLink, days)
        subq = link_counts.subquery()
        stmt = select(func.count()).select_from(subq).where(subq.c.link_count > 1)
        return self.session.execute(stmt).scalar_one()

    def list_duplicate_master_profiles(
        self,
        tenant_id: Optional[uuid.UUID] = None,
        skip: int = 0,
        limit: int = 100,
        days: Optional[int] = None,
    ) -> list[DuplicateMasterProfile]:
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
        stmt = self._filter_tenant(stmt, CdpMasterProfile, tenant_id)
        stmt = self._filter_recent(stmt, CdpMasterProfile, days)
        rows = self.session.execute(stmt).all()
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

    def identity_graph_coverage(
        self, tenant_id: Optional[uuid.UUID] = None, days: Optional[int] = None
    ) -> dict:
        """Counts how many master profiles have each identity channel populated."""
        total = self.count_master_profiles(tenant_id, days=days)

        def _count(condition) -> int:
            stmt = select(func.count()).select_from(CdpMasterProfile).where(condition)
            stmt = self._filter_tenant(stmt, CdpMasterProfile, tenant_id)
            stmt = self._filter_recent(stmt, CdpMasterProfile, days)
            return self.session.execute(stmt).scalar_one()

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
        self,
        tenant_id: Optional[uuid.UUID] = None,
        domain: Optional[str] = None,
        is_active: Optional[bool] = None,
        days: Optional[int] = None,
    ) -> dict:
        """Aggregate analytics for customer personas used by the Persona Management UI."""
        where_clauses = []
        if tenant_id is not None:
            where_clauses.append(CdpCustomerPersona.tenant_id == tenant_id)
        if domain is not None:
            where_clauses.append(CdpCustomerPersona.domain == domain)
        if is_active is not None:
            where_clauses.append(CdpCustomerPersona.is_active == is_active)

        cutoff = self._cutoff_for_days(days)
        if cutoff is not None:
            where_clauses.append(CdpCustomerPersona.computed_at >= cutoff)

        base = (
            select(
                CdpCustomerPersona.master_profile_id,
                CdpCustomerPersona.is_active,
                CdpCustomerPersona.domain,
                CdpCustomerPersona.persona_score,
                CdpCustomerPersona.confidence_score,
                CdpCustomerPersona.risk_level,
                CdpCustomerPersona.customer_value_tier,
                CdpPersonaArchetype.persona_category,
            )
            .join(CdpPersonaArchetype, CdpPersonaArchetype.persona_archetype_id == CdpCustomerPersona.persona_archetype_id)
            .where(*where_clauses)
            .subquery()
        )

        archetype_filters = []
        if tenant_id is not None:
            archetype_filters.append(CdpPersonaArchetype.tenant_id == tenant_id)
        if domain is not None:
            archetype_filters.append(CdpPersonaArchetype.domain == domain)
        total_archetypes = self.session.execute(
            select(func.count()).select_from(CdpPersonaArchetype).where(*archetype_filters)
        ).scalar_one()

        total_personas = self.session.execute(select(func.count()).select_from(base)).scalar_one()
        active_personas = self.session.execute(
            select(func.count()).select_from(base).where(base.c.is_active.is_(True))
        ).scalar_one()
        inactive_personas = max(0, total_personas - active_personas)
        unique_master_profiles = self.session.execute(
            select(func.count(func.distinct(base.c.master_profile_id))).select_from(base)
        ).scalar_one()

        avg_persona_score_raw, avg_confidence_score_raw = self.session.execute(
            select(func.avg(base.c.persona_score), func.avg(base.c.confidence_score)).select_from(base)
        ).one()

        def _bucket_rows(column_name: str) -> list[dict]:
            col = getattr(base.c, column_name)
            rows = self.session.execute(
                select(col, func.count().label("count"))
                .select_from(base)
                .group_by(col)
                .order_by(func.count().desc())
            ).all()
            return [{"value": (value or "unknown"), "count": count} for value, count in rows]

        return {
            "total_archetypes": total_archetypes,
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

    def get_cir_summary(
        self, tenant_id: Optional[uuid.UUID] = None, days: int = 90
    ) -> CirSummary:
        """One-shot overview: raw/master profile totals, processing funnel,
        domain/source breakdowns, and duplicate (merged) master profile count."""
        by_status = self.raw_profiles_by_status(tenant_id, days=days)
        status_counts = {row["status_code"]: row["count"] for row in by_status}

        return CirSummary(
            total_raw_profiles=self.count_raw_profiles(tenant_id, days=days),
            total_master_profiles=self.count_master_profiles(tenant_id, days=days),
            processed_raw_profiles=status_counts.get(3, 0),
            pending_raw_profiles=status_counts.get(1, 0),
            in_progress_raw_profiles=status_counts.get(2, 0),
            duplicate_master_profile_count=self.count_duplicate_master_profiles(tenant_id, days=days),
            raw_profiles_by_status=by_status,
            raw_profiles_by_domain=self.raw_profiles_by_domain(tenant_id, days=days),
            master_profiles_by_domain=self.master_profiles_by_domain(tenant_id, days=days),
            raw_profiles_by_source_system=self.raw_profiles_by_source_system(tenant_id, days=days),
        )

    def get_identity_graph_coverage(
        self, tenant_id: Optional[uuid.UUID] = None, days: int = 90
    ) -> IdentityGraphCoverage:
        """Adoption of each identity channel (email/phone/device/advertising/cookie/
        external id/national id) across all resolved master profiles."""
        return self.identity_graph_coverage(tenant_id, days=days)
