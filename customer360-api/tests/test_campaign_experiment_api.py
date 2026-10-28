"""HTTP contract tests for campaign experiment endpoints."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.database import get_db
from core.repositories.campaign_experiment_repository import (
    CampaignExperimentConflictError,
    CampaignExperimentNotFoundError,
)
from core.routers.campaign_experiment_api import all_campaign_experiment_routers

TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
CAMPAIGN_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
EXPERIMENT_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
VARIANT_A_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
VARIANT_B_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")
SEGMENT_A_ID = uuid.UUID("77777777-7777-7777-7777-777777777777")
SEGMENT_B_ID = uuid.UUID("88888888-8888-8888-8888-888888888888")


class FakeCampaignExperimentRepository:
    update_error = None

    @staticmethod
    def _read() -> dict:
        now = datetime.now(timezone.utc)
        return {
            "experiment_id": EXPERIMENT_ID,
            "tenant_id": TENANT_ID,
            "campaign_id": CAMPAIGN_ID,
            "name": "Audience test",
            "status": "Draft",
            "primary_metric": "conversions",
            "start_date": None,
            "end_date": None,
            "winning_variant_id": None,
            "created_by": USER_ID,
            "created_at": now,
            "updated_at": now,
            "variants": [
                {
                    "variant_id": VARIANT_A_ID,
                    "experiment_id": EXPERIMENT_ID,
                    "variant_key": "A",
                    "name": "Control",
                    "segment_id": SEGMENT_A_ID,
                    "template_id": None,
                    "allocation_percentage": "50.00",
                    "is_control": True,
                    "status": "Draft",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "variant_id": VARIANT_B_ID,
                    "experiment_id": EXPERIMENT_ID,
                    "variant_key": "B",
                    "name": "Variant B",
                    "segment_id": SEGMENT_B_ID,
                    "template_id": None,
                    "allocation_percentage": "50.00",
                    "is_control": False,
                    "status": "Draft",
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        }

    def __init__(self, session):
        self.session = session

    def list_for_campaign(self, tenant_id, campaign_id):
        return [self._read()] if tenant_id == TENANT_ID and campaign_id == CAMPAIGN_ID else []

    def create(self, tenant_id, campaign_id, created_by, payload):
        assert tenant_id == TENANT_ID
        assert campaign_id == CAMPAIGN_ID
        assert created_by == USER_ID
        return self._read()

    def get_experiment(self, tenant_id, experiment_id):
        if tenant_id != TENANT_ID or experiment_id != EXPERIMENT_ID:
            raise CampaignExperimentNotFoundError("not found")
        return SimpleNamespace()

    def read_experiment(self, experiment):
        return self._read()

    def update(self, tenant_id, experiment_id, editor_id, payload):
        if self.update_error:
            raise self.update_error
        return self._read()

    def performance(self, tenant_id, experiment_id):
        return [
            {
                "variant_id": VARIANT_A_ID,
                "variant_key": "A",
                "variant_name": "Control",
                "spend": "10.00",
                "impressions": 100,
                "clicks": 10,
                "conversions": 2,
                "revenue_estimated": "50.00",
                "conversion_rate": "20.00",
                "roas": "5.00",
            }
        ]


def build_app() -> FastAPI:
    app = FastAPI()
    for router in all_campaign_experiment_routers:
        app.include_router(router)

    @app.middleware("http")
    async def inject_identity(request: Request, call_next):
        request.state.tenant_id = str(TENANT_ID)
        request.state.user_id = str(USER_ID)
        return await call_next(request)

    app.dependency_overrides[get_db] = lambda: None
    return app


def test_create_experiment_contract():
    payload = {
        "name": "Audience test",
        "primary_metric": "conversions",
        "variants": [
            {"variant_key": "A", "name": "Control", "segment_id": str(SEGMENT_A_ID), "allocation_percentage": 50, "is_control": True},
            {"variant_key": "B", "name": "Variant B", "segment_id": str(SEGMENT_B_ID), "allocation_percentage": 50},
        ],
    }
    with patch("core.routers.campaign_experiment_api.CampaignExperimentRepository", FakeCampaignExperimentRepository):
        response = TestClient(build_app()).post(f"/campaigns/{CAMPAIGN_ID}/experiments", json=payload)
    assert response.status_code == 201
    assert len(response.json()["variants"]) == 2


def test_get_experiment_is_tenant_scoped():
    with patch("core.routers.campaign_experiment_api.CampaignExperimentRepository", FakeCampaignExperimentRepository):
        response = TestClient(build_app()).get(f"/campaign-experiments/{uuid.uuid4()}")
    assert response.status_code == 404


def test_update_experiment_conflict_is_409():
    FakeCampaignExperimentRepository.update_error = CampaignExperimentConflictError("stale")
    try:
        with patch("core.routers.campaign_experiment_api.CampaignExperimentRepository", FakeCampaignExperimentRepository):
            response = TestClient(build_app()).patch(f"/campaign-experiments/{EXPERIMENT_ID}", json={"status": "Running"})
        assert response.status_code == 409
    finally:
        FakeCampaignExperimentRepository.update_error = None


def test_experiment_performance_returns_variant_metrics():
    with patch("core.routers.campaign_experiment_api.CampaignExperimentRepository", FakeCampaignExperimentRepository):
        response = TestClient(build_app()).get(f"/campaign-experiments/{EXPERIMENT_ID}/performance")
    assert response.status_code == 200
    assert response.json()[0]["variant_key"] == "A"
