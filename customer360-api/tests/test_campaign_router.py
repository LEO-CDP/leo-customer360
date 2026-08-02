"""Unit tests for the /campaigns CRUD endpoints (core.routers.crm: campaigns_router).

Tests HTTP-level wiring (status codes, request/response schemas, filters) for
Campaign create/list/get/update/delete/count entirely against an in-memory
fake CRUD layer -- no real PostgreSQL instance required.
"""

import unittest
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import get_db
from core.models.crm import Campaign
from core.routers._generic import build_crud_router
from core.schemas.crm import CampaignCreate, CampaignRead, CampaignUpdate

DEMO_TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class FakeCampaignCRUD:
    """In-memory stand-in for CRUDBase(Campaign) -- no real DB needed."""

    store: dict[uuid.UUID, SimpleNamespace] = {}
    last_list_kwargs: dict[str, Any] = {}
    last_count_kwargs: dict[str, Any] = {}

    def __init__(self, model):
        self.model = model

    @classmethod
    def reset(cls):
        cls.store = {}
        cls.last_list_kwargs = {}
        cls.last_count_kwargs = {}

    def list(self, db, *, skip=0, limit=100, **filters):
        FakeCampaignCRUD.last_list_kwargs = filters
        return list(FakeCampaignCRUD.store.values())

    def count(self, db, **filters):
        FakeCampaignCRUD.last_count_kwargs = filters
        return len(FakeCampaignCRUD.store)

    def get(self, db, pk: uuid.UUID) -> Optional[SimpleNamespace]:
        return FakeCampaignCRUD.store.get(pk)

    def create(self, db, obj_in: dict[str, Any]) -> SimpleNamespace:
        obj = SimpleNamespace(
            campaign_id=uuid.uuid4(),
            created_at=datetime.now(timezone.utc),
            **obj_in,
        )
        FakeCampaignCRUD.store[obj.campaign_id] = obj
        return obj

    def update(self, db, db_obj: SimpleNamespace, obj_in: dict[str, Any]) -> SimpleNamespace:
        for field, value in obj_in.items():
            setattr(db_obj, field, value)
        return db_obj

    def delete(self, db, db_obj: SimpleNamespace) -> None:
        FakeCampaignCRUD.store.pop(db_obj.campaign_id, None)


def _build_test_app() -> FastAPI:
    with patch("core.routers._generic.CRUDBase", FakeCampaignCRUD):
        router = build_crud_router(
            model=Campaign,
            pk_field="campaign_id",
            pk_type=uuid.UUID,
            create_schema=CampaignCreate,
            update_schema=CampaignUpdate,
            read_schema=CampaignRead,
            prefix="/campaigns",
            tags=["CRM - Campaigns"],
        )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: None
    return app


def _campaign_payload(**overrides) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenant_id": str(DEMO_TENANT_ID),
        "name": "Q4 Banking App Install - Google UAC",
        "campaign_code": "BANK-Q4-GOOG-UAC-001",
        "status": "Active",
        "channel": "Paid Search",
        "platform": "Google",
        "objective": "App Install",
        "description": "Demo Google UAC campaign for banking app installs.",
        "start_date": "2025-10-01",
        "end_date": "2026-01-31",
        "budget_amount": "500000000.00",
        "currency": "VND",
        "keywords": ["banking", "app_install", "google"],
        "lang": "vi",
    }
    payload.update(overrides)
    return payload


class CampaignCrudTests(unittest.TestCase):
    def setUp(self):
        FakeCampaignCRUD.reset()
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)
        self.client = TestClient(_build_test_app())

    # ------------------------------------------------------------------
    # CREATE
    # ------------------------------------------------------------------

    def test_create_campaign_returns_201_with_generated_id(self):
        response = self.client.post("/campaigns/", json=_campaign_payload())

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn("campaign_id", body)
        self.assertEqual(body["name"], "Q4 Banking App Install - Google UAC")
        self.assertEqual(body["campaign_code"], "BANK-Q4-GOOG-UAC-001")
        self.assertEqual(body["status"], "Active")
        self.assertEqual(body["channel"], "Paid Search")
        self.assertEqual(body["platform"], "Google")
        self.assertEqual(body["objective"], "App Install")

    def test_create_campaign_missing_required_name_returns_422(self):
        payload = _campaign_payload()
        del payload["name"]

        response = self.client.post("/campaigns/", json=payload)

        self.assertEqual(response.status_code, 422)

    def test_create_campaign_missing_required_tenant_id_returns_422(self):
        payload = _campaign_payload()
        del payload["tenant_id"]

        response = self.client.post("/campaigns/", json=payload)

        self.assertEqual(response.status_code, 422)

    def test_create_campaign_optional_fields_can_be_omitted(self):
        payload = {"tenant_id": str(DEMO_TENANT_ID), "name": "Minimal Campaign"}

        response = self.client.post("/campaigns/", json=payload)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIsNone(body.get("campaign_code"))
        self.assertIsNone(body.get("status"))

    # ------------------------------------------------------------------
    # READ
    # ------------------------------------------------------------------

    def test_get_campaign_by_id_after_create(self):
        created = self.client.post("/campaigns/", json=_campaign_payload()).json()

        response = self.client.get(f"/campaigns/{created['campaign_id']}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["campaign_id"], created["campaign_id"])
        self.assertEqual(response.json()["platform"], "Google")

    def test_get_nonexistent_campaign_returns_404(self):
        response = self.client.get(f"/campaigns/{uuid.uuid4()}")

        self.assertEqual(response.status_code, 404)

    def test_list_campaigns_returns_all_created(self):
        self.client.post("/campaigns/", json=_campaign_payload(name="Campaign A", campaign_code="A"))
        self.client.post("/campaigns/", json=_campaign_payload(name="Campaign B", campaign_code="B"))

        response = self.client.get("/campaigns/")

        self.assertEqual(response.status_code, 200)
        names = {item["name"] for item in response.json()}
        self.assertEqual(names, {"Campaign A", "Campaign B"})

    def test_list_campaigns_passes_through_tenant_id_filter(self):
        tenant_id = str(uuid.uuid4())

        response = self.client.get(f"/campaigns/?tenant_id={tenant_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(FakeCampaignCRUD.last_list_kwargs, {"tenant_id": uuid.UUID(tenant_id)})

    def test_count_campaigns_reflects_number_created(self):
        self.client.post("/campaigns/", json=_campaign_payload(name="C1", campaign_code="C1"))
        self.client.post("/campaigns/", json=_campaign_payload(name="C2", campaign_code="C2"))

        response = self.client.get("/campaigns/count")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"count": 2})

    # ------------------------------------------------------------------
    # UPDATE
    # ------------------------------------------------------------------

    def test_update_campaign_status_and_budget(self):
        created = self.client.post("/campaigns/", json=_campaign_payload()).json()

        response = self.client.patch(
            f"/campaigns/{created['campaign_id']}",
            json={"status": "Paused", "budget_amount": "450000000.00"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "Paused")
        self.assertEqual(body["budget_amount"], "450000000.00")
        # untouched fields stay intact
        self.assertEqual(body["name"], "Q4 Banking App Install - Google UAC")

    def test_update_nonexistent_campaign_returns_404(self):
        response = self.client.patch(
            f"/campaigns/{uuid.uuid4()}",
            json={"status": "Paused"},
        )

        self.assertEqual(response.status_code, 404)

    def test_update_campaign_utm_metadata(self):
        created = self.client.post("/campaigns/", json=_campaign_payload()).json()

        response = self.client.patch(
            f"/campaigns/{created['campaign_id']}",
            json={"metadata_": {"utm_source": "google", "utm_medium": "cpc", "utm_campaign": "bank-q4-001"}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["metadata_"]["utm_source"], "google")

    # ------------------------------------------------------------------
    # DELETE
    # ------------------------------------------------------------------

    def test_delete_campaign_returns_204(self):
        created = self.client.post("/campaigns/", json=_campaign_payload()).json()

        response = self.client.delete(f"/campaigns/{created['campaign_id']}")

        self.assertEqual(response.status_code, 204)

    def test_delete_campaign_removes_it_from_list(self):
        created = self.client.post("/campaigns/", json=_campaign_payload()).json()
        self.client.delete(f"/campaigns/{created['campaign_id']}")

        response = self.client.get("/campaigns/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_delete_nonexistent_campaign_returns_404(self):
        response = self.client.delete(f"/campaigns/{uuid.uuid4()}")

        self.assertEqual(response.status_code, 404)

    def test_delete_and_get_returns_404(self):
        created = self.client.post("/campaigns/", json=_campaign_payload()).json()
        self.client.delete(f"/campaigns/{created['campaign_id']}")

        response = self.client.get(f"/campaigns/{created['campaign_id']}")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
