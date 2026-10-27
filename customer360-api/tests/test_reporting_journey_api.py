"""Unit tests for the /reporting/journey API endpoint."""

import uuid
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.database import get_db
from core.routers.reporting_api import router as reporting_router
from leo_customer360_dao.schemas.journey import JOURNEY_STAGES, JourneyReportResponse

TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _build_client(authenticated_tenant: uuid.UUID | None = TENANT_A) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        if authenticated_tenant is not None:
            request.state.tenant_id = str(authenticated_tenant)
        return await call_next(request)

    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.include_router(reporting_router)
    return TestClient(app)


def _sample_report() -> JourneyReportResponse:
    return JourneyReportResponse(
        report_date="2026-10-26",
        window_days=30,
        stages=[
            {
                "stage": stage,
                "label": stage.title(),
                "primary_metric": None,
                "metrics": [],
                "campaigns": [],
                "agents": [],
            }
            for stage in JOURNEY_STAGES
        ],
    )


def test_get_journey_report_uses_authenticated_tenant():
    client = _build_client(TENANT_A)
    with (
        patch("core.routers.reporting_api.get_redis_client", return_value=None, create=True),
        patch("core.routers.reporting_api.ReportingRepository") as repo_cls,
    ):
        repo_cls.return_value.get_journey_report.return_value = _sample_report()
        response = client.get("/reporting/journey?days=30&window=30")

    assert response.status_code == 200
    repo_cls.return_value.get_journey_report.assert_called_once_with(
        tenant_id=TENANT_A,
        days=30,
        window_days=30,
    )
    payload = response.json()
    assert payload["window_days"] == 30
    assert len(payload["stages"]) == 7


def test_get_journey_report_rejects_cross_tenant_query_param():
    client = _build_client(TENANT_A)
    response = client.get(f"/reporting/journey?tenant_id={TENANT_B}")
    assert response.status_code == 403


def test_get_journey_report_requires_tenant_context():
    client = _build_client(None)
    response = client.get("/reporting/journey")
    assert response.status_code == 400


def test_get_journey_report_rejects_invalid_window():
    client = _build_client(TENANT_A)
    with patch("core.routers.reporting_api.get_redis_client", return_value=None, create=True):
        response = client.get("/reporting/journey?window=15")
    assert response.status_code == 422
