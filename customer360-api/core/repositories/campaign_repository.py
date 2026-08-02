"""Campaign analytics repository: KPI summary, filtered pagination,
daily spend trend, and top-campaign queries against
customer360.vw_campaign_performance_metrics and
customer360.crm_campaign_performance_daily.

Uses the same synchronous SQLAlchemy Session as the rest of the API
(see core/database.py).  Row-Level Security is enforced at the DB layer;
the _set_tenant_context() call below explicitly sets the session variable
that the tenant_policy RLS policies key off.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import asc, and_, desc, func, select, text
from sqlalchemy.orm import Session

from core.models.crm import CRMCampaignPerformanceDaily, VwCampaignPerformanceMetrics
from core.schemas.crm import CampaignFilterParams

# Columns that callers are allowed to sort by (allowlist prevents SQLi via
# dynamic getattr).
_SORTABLE_COLUMNS = frozenset({
    "name", "status", "channel", "platform", "objective",
    "total_spend", "total_impressions", "total_clicks",
    "total_conversions", "total_revenue", "ctr_percentage",
    "cvr_percentage", "cpa", "roas",
})


class CampaignRepository:
    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.session = session
        self.tenant_id = tenant_id

    def _set_tenant_context(self) -> None:
        """Set app.tenant_id session variable so RLS policies take effect."""
        self.session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(self.tenant_id)},
        )

    def get_kpi_summary(self) -> dict:
        """Aggregate KPIs for the top dashboard cards."""
        self._set_tenant_context()

        stmt = select(
            func.count(VwCampaignPerformanceMetrics.campaign_id).label("total_campaigns"),
            func.coalesce(func.sum(VwCampaignPerformanceMetrics.total_spend), 0).label("total_spend"),
            func.coalesce(func.sum(VwCampaignPerformanceMetrics.total_impressions), 0).label("total_impressions"),
            func.coalesce(func.sum(VwCampaignPerformanceMetrics.total_clicks), 0).label("total_clicks"),
            func.coalesce(func.sum(VwCampaignPerformanceMetrics.total_conversions), 0).label("total_conversions"),
            func.coalesce(func.sum(VwCampaignPerformanceMetrics.total_revenue), 0).label("total_revenue"),
        ).where(VwCampaignPerformanceMetrics.tenant_id == self.tenant_id)

        res = self.session.execute(stmt).mappings().first()

        spend = Decimal(str(res["total_spend"]))
        impressions = int(res["total_impressions"])
        clicks = int(res["total_clicks"])
        conversions = int(res["total_conversions"])
        revenue = Decimal(str(res["total_revenue"]))

        overall_ctr = (Decimal(clicks) / Decimal(impressions) * Decimal("100")) if impressions > 0 else Decimal("0")
        overall_cvr = (Decimal(conversions) / Decimal(clicks) * Decimal("100")) if clicks > 0 else Decimal("0")
        overall_roas = (revenue / spend) if spend > 0 else Decimal("0")

        return {
            "total_campaigns": res["total_campaigns"],
            "total_spend": spend,
            "total_impressions": impressions,
            "total_clicks": clicks,
            "overall_ctr": round(overall_ctr, 2),
            "total_conversions": conversions,
            "overall_cvr": round(overall_cvr, 2),
            "total_revenue": revenue,
            "overall_roas": round(overall_roas, 2),
        }

    def get_filtered_campaigns(
        self, filters: CampaignFilterParams
    ) -> tuple[list[VwCampaignPerformanceMetrics], int]:
        """Paginated, filtered, sorted campaign metrics from the view."""
        self._set_tenant_context()

        conditions = [VwCampaignPerformanceMetrics.tenant_id == self.tenant_id]

        if filters.search:
            pattern = f"%{filters.search}%"
            conditions.append(
                VwCampaignPerformanceMetrics.name.ilike(pattern)
                | VwCampaignPerformanceMetrics.campaign_code.ilike(pattern)
            )
        if filters.status and filters.status != "All":
            conditions.append(VwCampaignPerformanceMetrics.status == filters.status)
        if filters.channel and filters.channel != "All":
            conditions.append(VwCampaignPerformanceMetrics.channel == filters.channel)
        if filters.platform and filters.platform != "All":
            conditions.append(VwCampaignPerformanceMetrics.platform == filters.platform)
        if filters.objective and filters.objective != "All":
            conditions.append(VwCampaignPerformanceMetrics.objective == filters.objective)

        where_clause = and_(*conditions)

        total: int = self.session.execute(
            select(func.count()).select_from(VwCampaignPerformanceMetrics).where(where_clause)
        ).scalar() or 0

        # Only allow whitelisted column names to avoid attribute-injection.
        sort_col_name = filters.sort_by if filters.sort_by in _SORTABLE_COLUMNS else "total_spend"
        sort_col = getattr(VwCampaignPerformanceMetrics, sort_col_name)
        order_clause = desc(sort_col) if filters.sort_order.lower() == "desc" else asc(sort_col)

        offset = (filters.page - 1) * filters.page_size
        rows = self.session.execute(
            select(VwCampaignPerformanceMetrics)
            .where(where_clause)
            .order_by(order_clause)
            .offset(offset)
            .limit(filters.page_size)
        ).scalars().all()

        return list(rows), total

    def get_daily_spend_trend(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> list[dict]:
        """Daily spend time-series for the Spend Trend chart."""
        self._set_tenant_context()

        stmt = (
            select(
                CRMCampaignPerformanceDaily.report_date,
                func.sum(CRMCampaignPerformanceDaily.spend).label("spend"),
            )
            .where(CRMCampaignPerformanceDaily.tenant_id == self.tenant_id)
            .group_by(CRMCampaignPerformanceDaily.report_date)
            .order_by(CRMCampaignPerformanceDaily.report_date.asc())
        )
        if start_date:
            stmt = stmt.where(CRMCampaignPerformanceDaily.report_date >= start_date)
        if end_date:
            stmt = stmt.where(CRMCampaignPerformanceDaily.report_date <= end_date)

        return [
            {"report_date": row.report_date, "spend": row.spend}
            for row in self.session.execute(stmt).all()
        ]

    def get_top_campaigns(self, limit: int = 5) -> list[dict]:
        """Top campaigns by conversions and ROAS for the analytics charts."""
        self._set_tenant_context()

        stmt = (
            select(
                VwCampaignPerformanceMetrics.campaign_id,
                VwCampaignPerformanceMetrics.name,
                VwCampaignPerformanceMetrics.total_conversions.label("conversions"),
                VwCampaignPerformanceMetrics.roas,
            )
            .where(VwCampaignPerformanceMetrics.tenant_id == self.tenant_id)
            .order_by(desc(VwCampaignPerformanceMetrics.total_conversions))
            .limit(limit)
        )
        return [
            {
                "campaign_id": row.campaign_id,
                "name": row.name,
                "conversions": row.conversions,
                "roas": row.roas,
            }
            for row in self.session.execute(stmt).all()
        ]
