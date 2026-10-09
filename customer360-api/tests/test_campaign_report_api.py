"""Unit tests for the campaign report and analytics routes (core.routers.crm_api).

CrmRepository is replaced with a fake, so these tests cover HTTP wiring and
tenant derivation only -- no PostgreSQL required. One test exercises the real
CrmRepository.get_campaign_report composition with stand-in collaborators.
"""

import unittest
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.database import get_db
from core.repositories.campaign_draft_repository import CampaignDraftNotFoundError
from core.repositories.crm_repository import CrmRepository
from core.routers.crm_api import campaign_analytics_router, campaign_report_router

DEMO_TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_TENANT_ID = uuid.UUID("99999999-9999-9999-9999-999999999999")
CAMPAIGN_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _totals(**overrides):
    totals = dict(
        spend="100.00", impressions=1000, clicks=50, conversions=5, revenue="400.00",
        ctr_percentage="5.00", cvr_percentage="10.00", cpa="20.00", roas="4.00", zero_conversion_warning=False,
    )
    totals.update(overrides)
    return totals


def _campaign():
    return SimpleNamespace(
        campaign_id=CAMPAIGN_ID, tenant_id=DEMO_TENANT_ID, name="Q4 Win-Back", status="Draft",
        approval_status="Approved", approved_by=None, approved_at=None, created_at=NOW, updated_at=NOW,
        metadata_={"agent_provenance": {"agent_code": "campaign_planner_uat"}}, content_items=[],
    )


def _report(lifetime=None):
    return {
        "campaign": _campaign(),
        "coverage": {
            "start_date": None, "end_date": None, "first_report_date": date(2026, 1, 1),
            "last_report_date": date(2026, 1, 2), "days_with_data": 2,
        },
        "daily": [
            {"report_date": date(2026, 1, 1), "spend": "50.00", "impressions": 500, "clicks": 25, "conversions": 2, "revenue": "200.00"},
        ],
        "period_totals": _totals(),
        "lifetime": lifetime or _totals(),
        "content_plan": [{"content_item_id": str(uuid.uuid4()), "position": 1, "role": "primary", "title": "T"}],
        "approval": {"approval_status": "Approved", "last_review": {"type": "review"}, "audit_event_count": 3, "review_count": 1},
    }


class FakeCrmRepository:
    """Stand-in for CrmRepository that records the tenant it was built with."""

    tenants: list = []
    report_calls: list = []
    report_side_effect = None
    report_result = None

    def __init__(self, session, tenant_id):
        FakeCrmRepository.tenants.append(tenant_id)
        self.analytics = MagicMock()
        self.analytics.get_kpi_summary.return_value = {
            "total_campaigns": 0, "total_spend": "0", "total_impressions": 0,
            "total_clicks": 0, "total_conversions": 0, "total_revenue": "0",
            "overall_ctr": "0", "overall_cvr": "0", "overall_roas": "0",
        }
        self.analytics.get_filtered_campaigns.return_value = ([], 0)

    @classmethod
    def reset(cls):
        cls.tenants = []
        cls.report_calls = []
        cls.report_side_effect = None
        cls.report_result = _report()

    def get_campaign_report(self, campaign_id, start_date=None, end_date=None):
        FakeCrmRepository.report_calls.append((campaign_id, start_date, end_date))
        if FakeCrmRepository.report_side_effect is not None:
            raise FakeCrmRepository.report_side_effect
        return FakeCrmRepository.report_result


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(campaign_analytics_router)
    app.include_router(campaign_report_router)

    @app.middleware("http")
    async def _inject_identity(request: Request, call_next):
        request.state.tenant_id = request.headers.get("X-Tenant-Id", str(DEMO_TENANT_ID))
        return await call_next(request)

    app.dependency_overrides[get_db] = lambda: None
    return app


class CampaignReportApiTests(unittest.TestCase):
    def setUp(self):
        FakeCrmRepository.reset()
        patcher = patch("core.routers.crm_api.CrmRepository", FakeCrmRepository)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(_build_test_app())

    def test_report_returns_expected_shape(self):
        response = self.client.get(f"/campaigns/{CAMPAIGN_ID}/report")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        for key in ("campaign", "coverage", "daily", "period_totals", "lifetime", "content_plan", "approval"):
            self.assertIn(key, body)
        self.assertEqual(body["campaign"]["metadata_"]["agent_provenance"]["agent_code"], "campaign_planner_uat")
        self.assertEqual(body["coverage"]["days_with_data"], 2)
        self.assertEqual(body["approval"]["review_count"], 1)

    def test_zero_conversion_lifetime_has_null_cpa_and_warning(self):
        FakeCrmRepository.report_result = _report(lifetime=_totals(conversions=0, cpa=None, zero_conversion_warning=True))

        body = self.client.get(f"/campaigns/{CAMPAIGN_ID}/report").json()

        self.assertIsNone(body["lifetime"]["cpa"])
        self.assertTrue(body["lifetime"]["zero_conversion_warning"])

    def test_unknown_campaign_returns_404(self):
        FakeCrmRepository.report_side_effect = CampaignDraftNotFoundError("Campaign not found")

        self.assertEqual(self.client.get(f"/campaigns/{CAMPAIGN_ID}/report").status_code, 404)

    def test_end_before_start_returns_422(self):
        response = self.client.get(f"/campaigns/{CAMPAIGN_ID}/report?start_date=2026-02-01&end_date=2026-01-01")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(FakeCrmRepository.report_calls, [])

    def test_report_passes_date_window(self):
        self.client.get(f"/campaigns/{CAMPAIGN_ID}/report?start_date=2026-01-01&end_date=2026-01-31")

        self.assertEqual(FakeCrmRepository.report_calls, [(CAMPAIGN_ID, date(2026, 1, 1), date(2026, 1, 31))])

    def test_report_uses_request_tenant_not_query(self):
        self.client.get(f"/campaigns/{CAMPAIGN_ID}/report?tenant_id={OTHER_TENANT_ID}")

        self.assertEqual(FakeCrmRepository.tenants, [DEMO_TENANT_ID])


class CampaignAnalyticsTenantTests(unittest.TestCase):
    def setUp(self):
        FakeCrmRepository.reset()
        patcher = patch("core.routers.crm_api.CrmRepository", FakeCrmRepository)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(_build_test_app())

    def test_list_ignores_query_tenant(self):
        response = self.client.get(f"/campaigns/analytics?tenant_id={OTHER_TENANT_ID}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(FakeCrmRepository.tenants, [DEMO_TENANT_ID])

    def test_summary_ignores_query_tenant(self):
        response = self.client.get(f"/campaigns/analytics/summary?tenant_id={OTHER_TENANT_ID}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(FakeCrmRepository.tenants, [DEMO_TENANT_ID])


class CrmRepositoryReportCompositionTests(unittest.TestCase):
    def test_get_campaign_report_composes_coverage_and_approval(self):
        campaign = _campaign()
        campaign.approved_by = uuid.uuid4()
        campaign.approved_at = NOW
        reviews = [{"type": "review", "action": "reject"}, {"type": "review", "action": "approve"}]
        history = [{"type": "audit"}, reviews[0], {"type": "audit"}, reviews[1]]
        daily = [{"report_date": date(2026, 1, 3)}, {"report_date": date(2026, 1, 1)}]

        repo = CrmRepository(MagicMock(), DEMO_TENANT_ID)
        repo.drafts = MagicMock()
        repo.analytics = MagicMock()
        repo.drafts.get_campaign.return_value = campaign
        repo.drafts.list_campaign_history.return_value = history
        repo.drafts.list_campaign_content_items.return_value = ["item"]
        repo.analytics.get_campaign_performance.return_value = {"daily": daily, "period_totals": {"p": 1}, "lifetime": {"l": 1}}

        report = repo.get_campaign_report(CAMPAIGN_ID, date(2026, 1, 1), date(2026, 1, 31))

        repo.drafts.get_campaign.assert_called_once_with(DEMO_TENANT_ID, CAMPAIGN_ID)
        self.assertEqual(report["coverage"]["first_report_date"], date(2026, 1, 1))
        self.assertEqual(report["coverage"]["last_report_date"], date(2026, 1, 3))
        self.assertEqual(report["coverage"]["days_with_data"], 2)
        self.assertEqual(report["approval"]["last_review"], reviews[-1])
        self.assertEqual(report["approval"]["review_count"], 2)
        self.assertEqual(report["approval"]["audit_event_count"], 2)
        self.assertEqual(report["approval"]["approved_by"], campaign.approved_by)
        self.assertEqual(report["content_plan"], ["item"])
        self.assertEqual(report["lifetime"], {"l": 1})


if __name__ == "__main__":
    unittest.main()
