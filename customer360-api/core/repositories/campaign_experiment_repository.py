"""Tenant-scoped campaign A/B experiment persistence and performance queries."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from core.repositories.campaign_draft_repository import CampaignDraftNotFoundError
from leo_customer360_dao.models.crm import (
    Campaign,
    CampaignExperiment,
    CampaignExperimentVariant,
    CRMCampaignPerformanceDaily,
    MessageTemplate,
)
from leo_customer360_dao.models.system import SysAuditLog
from leo_customer360_dao.repositories.segment_respository import SegmentRepository


class CampaignExperimentError(ValueError):
    """Raised when an experiment definition is invalid for its campaign."""


class CampaignExperimentNotFoundError(LookupError):
    """Raised when an experiment is not visible in the active tenant."""


class CampaignExperimentConflictError(RuntimeError):
    """Raised when an experiment was changed after the editor loaded it."""


class CampaignExperimentRepository:
    def __init__(self, session: Session):
        self.session = session

    def _get_campaign(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign:
        campaign = self.session.execute(
            select(Campaign).where(Campaign.tenant_id == tenant_id, Campaign.campaign_id == campaign_id)
        ).scalar_one_or_none()
        if campaign is None:
            raise CampaignDraftNotFoundError(f"Campaign '{campaign_id}' not found")
        return campaign

    def get_experiment(self, tenant_id: uuid.UUID, experiment_id: uuid.UUID) -> CampaignExperiment:
        experiment = self.session.execute(
            select(CampaignExperiment).where(
                CampaignExperiment.tenant_id == tenant_id,
                CampaignExperiment.experiment_id == experiment_id,
            )
        ).scalar_one_or_none()
        if experiment is None:
            raise CampaignExperimentNotFoundError(f"Experiment '{experiment_id}' not found")
        return experiment

    def _validate_variants(self, tenant_id: uuid.UUID, variants: list[dict[str, Any]]) -> None:
        if len(variants) < 2:
            raise CampaignExperimentError("An experiment requires at least two variants")
        keys = [str(item["variant_key"]).strip() for item in variants]
        if len(keys) != len(set(keys)):
            raise CampaignExperimentError("Experiment variant keys must be unique")
        total = sum((Decimal(str(item["allocation_percentage"])) for item in variants), Decimal("0"))
        if total != Decimal("100"):
            raise CampaignExperimentError("Variant allocation percentages must total 100")
        if sum(1 for item in variants if item.get("is_control")) != 1:
            raise CampaignExperimentError("An experiment requires exactly one control variant")
        segment_repo = SegmentRepository(self.session)
        for item in variants:
            segment = segment_repo.get_segment(item["segment_id"])
            if segment is None or segment.tenant_id != tenant_id:
                raise CampaignExperimentError("Every experiment segment must belong to the active tenant")
            if not segment.is_active or segment.status_code != 1:
                raise CampaignExperimentError(
                    f"Experiment segment '{item['segment_id']}' is inactive or has no computed membership snapshot"
                )
            template_id = item.get("template_id")
            if template_id is not None:
                template = self.session.execute(
                    select(MessageTemplate).where(
                        MessageTemplate.tenant_id == tenant_id,
                        MessageTemplate.template_id == template_id,
                    )
                ).scalar_one_or_none()
                if template is None or template.status != "Approved":
                    raise CampaignExperimentError(
                        f"Experiment template '{template_id}' must belong to the active tenant and be Approved"
                    )

    @staticmethod
    def _variant_read(variant: CampaignExperimentVariant) -> dict[str, Any]:
        return {
            "variant_id": variant.variant_id,
            "experiment_id": variant.experiment_id,
            "variant_key": variant.variant_key,
            "name": variant.name,
            "segment_id": variant.segment_id,
            "template_id": variant.template_id,
            "allocation_percentage": variant.allocation_percentage,
            "is_control": variant.is_control,
            "status": variant.status,
            "created_at": variant.created_at,
            "updated_at": variant.updated_at,
        }

    def read_experiment(self, experiment: CampaignExperiment) -> dict[str, Any]:
        variants = self.session.execute(
            select(CampaignExperimentVariant)
            .where(
                CampaignExperimentVariant.tenant_id == experiment.tenant_id,
                CampaignExperimentVariant.experiment_id == experiment.experiment_id,
            )
            .order_by(CampaignExperimentVariant.variant_key.asc())
        ).scalars().all()
        return {
            "experiment_id": experiment.experiment_id,
            "tenant_id": experiment.tenant_id,
            "campaign_id": experiment.campaign_id,
            "name": experiment.name,
            "status": experiment.status,
            "primary_metric": experiment.primary_metric,
            "start_date": experiment.start_date,
            "end_date": experiment.end_date,
            "winning_variant_id": experiment.winning_variant_id,
            "variants": [self._variant_read(item) for item in variants],
            "created_by": experiment.created_by,
            "created_at": experiment.created_at,
            "updated_at": experiment.updated_at,
        }

    def list_for_campaign(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> list[dict[str, Any]]:
        self._get_campaign(tenant_id, campaign_id)
        experiments = self.session.execute(
            select(CampaignExperiment)
            .where(CampaignExperiment.tenant_id == tenant_id, CampaignExperiment.campaign_id == campaign_id)
            .order_by(CampaignExperiment.created_at.desc())
        ).scalars().all()
        return [self.read_experiment(item) for item in experiments]

    def create(
        self,
        tenant_id: uuid.UUID,
        campaign_id: uuid.UUID,
        created_by: uuid.UUID | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        campaign = self._get_campaign(tenant_id, campaign_id)
        variants = payload["variants"]
        self._validate_variants(tenant_id, variants)
        if payload.get("start_date") and payload.get("end_date") and payload["end_date"] < payload["start_date"]:
            raise CampaignExperimentError("Experiment end_date cannot be before start_date")
        experiment = CampaignExperiment(
            tenant_id=tenant_id,
            campaign_id=campaign.campaign_id,
            name=payload["name"],
            primary_metric=payload.get("primary_metric", "conversions"),
            start_date=payload.get("start_date"),
            end_date=payload.get("end_date"),
            created_by=created_by,
        )
        self.session.add(experiment)
        self.session.flush()
        for item in variants:
            self.session.add(
                CampaignExperimentVariant(
                    tenant_id=tenant_id,
                    experiment_id=experiment.experiment_id,
                    variant_key=item["variant_key"].strip(),
                    name=item["name"].strip(),
                    segment_id=item["segment_id"],
                    template_id=item.get("template_id"),
                    allocation_percentage=item["allocation_percentage"],
                    is_control=item.get("is_control", False),
                )
            )
        self.session.add(
            SysAuditLog(
                tenant_id=tenant_id,
                user_id=created_by,
                action="CREATE",
                resource_type="crm_campaign_experiment",
                resource_id=str(experiment.experiment_id),
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                after_data={
                    "campaign_id": str(campaign_id),
                    "name": experiment.name,
                    "primary_metric": experiment.primary_metric,
                    "variants": [
                        {
                            "variant_key": item["variant_key"],
                            "name": item["name"],
                            "segment_id": str(item["segment_id"]),
                            "allocation_percentage": str(item["allocation_percentage"]),
                            "is_control": item.get("is_control", False),
                        }
                        for item in variants
                    ],
                },
            )
        )
        self.session.commit()
        self.session.refresh(experiment)
        return self.read_experiment(experiment)

    def update(
        self,
        tenant_id: uuid.UUID,
        experiment_id: uuid.UUID,
        editor_id: uuid.UUID | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        experiment = self.get_experiment(tenant_id, experiment_id)
        expected_updated_at = payload.get("updated_at")
        if expected_updated_at is not None and experiment.updated_at != expected_updated_at:
            raise CampaignExperimentConflictError(
                f"Experiment '{experiment_id}' was modified; reload before updating it"
            )
        before = {
            "status": experiment.status,
            "winning_variant_id": str(experiment.winning_variant_id) if experiment.winning_variant_id else None,
            "end_date": experiment.end_date.isoformat() if experiment.end_date else None,
        }
        winning_variant_id = payload["winning_variant_id"] if "winning_variant_id" in payload else experiment.winning_variant_id
        if winning_variant_id is not None:
            variant = self.session.execute(
                select(CampaignExperimentVariant).where(
                    CampaignExperimentVariant.tenant_id == tenant_id,
                    CampaignExperimentVariant.experiment_id == experiment_id,
                    CampaignExperimentVariant.variant_id == winning_variant_id,
                )
            ).scalar_one_or_none()
            if variant is None:
                raise CampaignExperimentError("Winning variant does not belong to this experiment")
        allowed_statuses = {
            "Draft": {"Draft", "Running", "Cancelled"},
            "Running": {"Running", "Paused", "Completed", "Cancelled"},
            "Paused": {"Paused", "Running", "Completed", "Cancelled"},
            "Completed": {"Completed"},
            "Cancelled": {"Cancelled"},
        }
        next_status = payload.get("status", experiment.status)
        if next_status not in allowed_statuses.get(experiment.status, {experiment.status}):
            raise CampaignExperimentError(
                f"Experiment status cannot transition from {experiment.status} to {next_status}"
            )
        if next_status == "Completed" and winning_variant_id is None:
            raise CampaignExperimentError("A completed experiment must have a winning variant")
        if "end_date" in payload:
            end_date = payload["end_date"]
            if experiment.start_date and end_date and end_date < experiment.start_date:
                raise CampaignExperimentError("Experiment end_date cannot be before start_date")
            experiment.end_date = end_date
        experiment.status = next_status
        experiment.winning_variant_id = winning_variant_id
        experiment.updated_at = datetime.now(timezone.utc)
        after = {
            "status": experiment.status,
            "winning_variant_id": str(experiment.winning_variant_id) if experiment.winning_variant_id else None,
            "end_date": experiment.end_date.isoformat() if experiment.end_date else None,
        }
        self.session.add(
            SysAuditLog(
                tenant_id=tenant_id,
                user_id=editor_id,
                action="UPDATE",
                resource_type="crm_campaign_experiment",
                resource_id=str(experiment_id),
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                before_data=before,
                after_data=after,
            )
        )
        self.session.commit()
        self.session.refresh(experiment)
        return self.read_experiment(experiment)

    def performance(self, tenant_id: uuid.UUID, experiment_id: uuid.UUID) -> list[dict[str, Any]]:
        experiment = self.get_experiment(tenant_id, experiment_id)
        performance_join = and_(
            CRMCampaignPerformanceDaily.experiment_variant_id == CampaignExperimentVariant.variant_id,
            CRMCampaignPerformanceDaily.campaign_id == experiment.campaign_id,
        )
        if experiment.start_date is not None:
            performance_join = and_(
                performance_join,
                CRMCampaignPerformanceDaily.report_date >= experiment.start_date,
            )
        if experiment.end_date is not None:
            performance_join = and_(
                performance_join,
                CRMCampaignPerformanceDaily.report_date <= experiment.end_date,
            )
        stmt = (
            select(
                CampaignExperimentVariant.variant_id,
                CampaignExperimentVariant.variant_key,
                CampaignExperimentVariant.name,
                func.coalesce(func.sum(CRMCampaignPerformanceDaily.spend), 0).label("spend"),
                func.coalesce(func.sum(CRMCampaignPerformanceDaily.impressions), 0).label("impressions"),
                func.coalesce(func.sum(CRMCampaignPerformanceDaily.clicks), 0).label("clicks"),
                func.coalesce(func.sum(CRMCampaignPerformanceDaily.conversions), 0).label("conversions"),
                func.coalesce(func.sum(CRMCampaignPerformanceDaily.revenue_estimated), 0).label("revenue_estimated"),
            )
            .outerjoin(
                CRMCampaignPerformanceDaily,
                performance_join,
            )
            .where(
                CampaignExperimentVariant.tenant_id == tenant_id,
                CampaignExperimentVariant.experiment_id == experiment.experiment_id,
            )
            .group_by(
                CampaignExperimentVariant.variant_id,
                CampaignExperimentVariant.variant_key,
                CampaignExperimentVariant.name,
            )
            .order_by(CampaignExperimentVariant.variant_key.asc())
        )
        results = []
        for row in self.session.execute(stmt).all():
            clicks = int(row.clicks or 0)
            spend = Decimal(str(row.spend or 0))
            revenue = Decimal(str(row.revenue_estimated or 0))
            conversions = int(row.conversions or 0)
            results.append(
                {
                    "variant_id": row.variant_id,
                    "variant_key": row.variant_key,
                    "variant_name": row.name,
                    "spend": spend,
                    "impressions": int(row.impressions or 0),
                    "clicks": clicks,
                    "conversions": conversions,
                    "revenue_estimated": revenue,
                    "conversion_rate": round(Decimal(conversions) / Decimal(clicks) * Decimal("100"), 2) if clicks else Decimal("0"),
                    "roas": round(revenue / spend, 2) if spend else Decimal("0"),
                }
            )
        return results
