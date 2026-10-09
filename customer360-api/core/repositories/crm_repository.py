"""API-layer CRM orchestration and analytics facade."""

from leo_customer360_dao.repositories.campaign_repository import CampaignRepository as DaoCampaignRepository

from core.repositories.campaign_draft_repository import (
    APPROVAL_STATUS_APPROVED,
    CampaignDraftRepository,
)


class CrmRepository:
    """Coordinate campaign draft enrichment and DAO campaign analytics."""

    def __init__(self, session, tenant_id=None):
        """Create a CRM facade for a session and optional analytics tenant."""
        self.session = session
        self.tenant_id = tenant_id
        self.drafts = CampaignDraftRepository(session)
        self.analytics = DaoCampaignRepository(session, tenant_id) if tenant_id is not None else None

    def list_campaign_content_items(self, campaign):
        """Return the content plan attached to a campaign."""
        return self.drafts.list_campaign_content_items(campaign.tenant_id, campaign.campaign_id)

    def validate_create_campaign(self, payload):
        """Reject generic campaign writes that bypass draft approval."""
        if payload.get("approval_status") not in (None, "Draft"):
            raise ValueError("Campaigns created via POST /campaigns must start in Draft; use the campaign draft approval workflow to change approval_status")
        if payload.get("approved_by") is not None or payload.get("approved_at") is not None:
            raise ValueError("Campaign approval metadata may not be set via POST /campaigns; use the campaign draft approval workflow instead")

    def validate_campaign_update(self, campaign, payload):
        """Reject edits to campaigns already approved."""
        if campaign.approval_status == APPROVAL_STATUS_APPROVED:
            raise ValueError(f"Campaign '{campaign.campaign_id}' is Approved; edit it via PATCH /campaigns/{{campaign_id}}/draft instead, which re-reviews the change and records it in the campaign's history")

    def get_kpi_summary(self):
        """Return campaign KPI analytics."""
        return self.analytics.get_kpi_summary()

    def get_filtered_campaigns(self, filters):
        """Return filtered campaign metrics and their total count."""
        return self.analytics.get_filtered_campaigns(filters)

    def get_daily_spend_trend(self, start_date=None, end_date=None):
        """Return daily campaign spend metrics."""
        return self.analytics.get_daily_spend_trend(start_date=start_date, end_date=end_date)

    def get_top_campaigns(self, limit=5):
        """Return the top campaign metrics."""
        return self.analytics.get_top_campaigns(limit=limit)

    def get_campaign_report(self, campaign_id, start_date=None, end_date=None):
        """One tenant-owned campaign's report; raises CampaignDraftNotFoundError
        for a missing or other-tenant campaign."""
        campaign = self.drafts.get_campaign(self.tenant_id, campaign_id)
        performance = self.analytics.get_campaign_performance(campaign_id, start_date, end_date)
        history = self.drafts.list_campaign_history(self.tenant_id, campaign_id)
        reviews = [entry for entry in history if entry["type"] == "review"]
        report_dates = [day["report_date"] for day in performance["daily"]]
        return {
            "campaign": campaign,
            "coverage": {
                "start_date": start_date,
                "end_date": end_date,
                "first_report_date": min(report_dates, default=None),
                "last_report_date": max(report_dates, default=None),
                "days_with_data": len(report_dates),
            },
            "daily": performance["daily"],
            "period_totals": performance["period_totals"],
            "lifetime": performance["lifetime"],
            "content_plan": self.drafts.list_campaign_content_items(self.tenant_id, campaign_id),
            "approval": {
                "approval_status": campaign.approval_status,
                "approved_by": campaign.approved_by,
                "approved_at": campaign.approved_at,
                "last_review": reviews[-1] if reviews else None,
                "audit_event_count": len(history) - len(reviews),
                "review_count": len(reviews),
            },
        }
