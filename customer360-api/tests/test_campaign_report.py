"""Campaign report HTTP contract and tenant/date-scoped aggregation tests."""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from core.database import get_db
from core.routers.crm_api import campaigns_router
from leo_customer360_dao.repositories.campaign_repository import CampaignRepository

TENANT_ID = uuid.uuid4()
CAMPAIGN_ID = uuid.uuid4()


def campaign():
    return {
        "campaign_id": CAMPAIGN_ID, "name": "UAT report", "campaign_code": None,
        "status": "Draft", "channel": "email", "platform": "email", "objective": None,
    }


def repository(daily, exists=True):
    session = MagicMock()
    session.execute.return_value.mappings.return_value.first.return_value = campaign() if exists else None
    session.execute.return_value.mappings.return_value.all.return_value = daily
    return CampaignRepository(session, TENANT_ID), session


def test_report_aggregates_metrics_and_scopes_both_queries():
    repo, session = repository([
        {"report_date": date(2026, 10, 1), "spend": Decimal("20"), "impressions": 100,
         "clicks": 10, "conversions": 2, "revenue_estimated": Decimal("50")},
        {"report_date": date(2026, 10, 2), "spend": Decimal("30"), "impressions": 300,
         "clicks": 10, "conversions": 3, "revenue_estimated": Decimal("100")},
    ])
    report = repo.get_campaign_report(CAMPAIGN_ID, date(2026, 10, 1), date(2026, 10, 2))
    summary = report["summary"]
    assert summary["total_spend"] == 50
    assert summary["total_impressions"] == 400
    assert summary["total_clicks"] == 20
    assert summary["total_conversions"] == 5
    assert summary["total_revenue"] == 150
    assert summary["ctr_percentage"] == 5
    assert summary["cvr_percentage"] == 25
    assert summary["cpa"] == 10
    assert summary["roas"] == 3
    queries = [call.args[0].compile(dialect=postgresql.dialect()) for call in session.execute.call_args_list[1:]]
    for query in queries:
        assert TENANT_ID in query.params.values()
        assert CAMPAIGN_ID in query.params.values()
    sql = str(queries[1])
    assert "report_date >=" in sql and "report_date <=" in sql
    assert "GROUP BY" in sql and "ORDER BY" in sql


def test_empty_report_is_zero_not_missing():
    repo, _ = repository([])
    report = repo.get_campaign_report(CAMPAIGN_ID)
    assert report["daily"] == []
    assert report["summary"]["campaign_id"] == CAMPAIGN_ID
    for key in ("total_spend", "total_revenue", "ctr_percentage", "cvr_percentage", "cpa", "roas"):
        assert report["summary"][key] == 0


def test_missing_campaign_does_not_query_performance():
    repo, session = repository([], exists=False)
    assert repo.get_campaign_report(CAMPAIGN_ID) is None
    assert session.execute.call_count == 2


def test_invalid_date_range_is_rejected_before_query():
    repo, session = repository([])
    with pytest.raises(ValueError, match="End date"):
        repo.get_campaign_report(CAMPAIGN_ID, date(2026, 10, 2), date(2026, 10, 1))
    session.execute.assert_not_called()


def client():
    app = FastAPI()
    app.include_router(campaigns_router)

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.tenant_id = str(TENANT_ID)
        return await call_next(request)

    app.dependency_overrides[get_db] = lambda: MagicMock()
    return TestClient(app)


def test_report_route_returns_expected_shape_and_uses_authenticated_tenant():
    repo, _ = repository([])
    report = repo.get_campaign_report(CAMPAIGN_ID)
    with patch("core.routers.crm_api.CrmRepository") as factory:
        factory.return_value.get_campaign_report.return_value = report
        response = client().get(f"/campaigns/{CAMPAIGN_ID}/report?tenant_id={uuid.uuid4()}&start_date=2026-10-01")
    assert response.status_code == 200, response.text
    assert response.json()["summary"]["campaign_id"] == str(CAMPAIGN_ID)
    assert response.json()["daily"] == []
    assert factory.call_args.args[1] == TENANT_ID
    factory.return_value.get_campaign_report.assert_called_once_with(CAMPAIGN_ID, date(2026, 10, 1), None)


def test_report_route_returns_404_for_unknown_or_other_tenant_campaign():
    with patch("core.routers.crm_api.CrmRepository") as factory:
        factory.return_value.get_campaign_report.return_value = None
        response = client().get(f"/campaigns/{CAMPAIGN_ID}/report")
    assert response.status_code == 404


def test_report_route_maps_invalid_dates_to_422():
    with patch("core.routers.crm_api.CrmRepository") as factory:
        factory.return_value.get_campaign_report.side_effect = ValueError("End date cannot be before start date.")
        response = client().get(f"/campaigns/{CAMPAIGN_ID}/report?start_date=2026-10-02&end_date=2026-10-01")
    assert response.status_code == 422


def test_campaign_state_refresh_bypasses_response_cache():
    stored = SimpleNamespace(
        **campaign(), tenant_id=TENANT_ID, approval_status="Approved",
    )
    with patch("core.cache.cache_get") as cached, patch(
        "core.routers._generic.CRUDBase.get", return_value=stored,
    ), patch("core.routers.crm_api.CrmRepository") as repository:
        repository.return_value.list_campaign_content_items.return_value = []
        http = client()
        first = http.get(f"/campaigns/{CAMPAIGN_ID}")
        stored.status = "Completed"
        second = http.get(f"/campaigns/{CAMPAIGN_ID}")
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == "Draft"
    assert second.json()["status"] == "Completed"
    cached.assert_not_called()
