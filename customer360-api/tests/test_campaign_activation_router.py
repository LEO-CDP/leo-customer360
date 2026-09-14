"""HTTP-level tests for the campaign activation + email dispatch admin router
(core.routers.campaign_activation_api). The Dagster submit and the provider-
config CRUD are mocked -- these verify request/response wiring, the tenant scope
and the approval gate, not the pipeline itself."""

import unittest
import uuid
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import get_db
from core.routers.campaign_activation_api import campaign_activation_router


class CampaignActivationRouterTests(unittest.TestCase):
    def setUp(self):
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        self.tenant_id = str(uuid.uuid4())
        self.campaign_id = uuid.uuid4()

        self.app = FastAPI()
        self.app.include_router(campaign_activation_router)

        @self.app.middleware("http")
        async def _inject_identity(request, call_next):
            request.state.tenant_id = self.tenant_id
            # SSO is forced on in CI; inject a tenant-admin so we reach the handler.
            request.state.user = {"roles": ["tenant_admin"]}
            return await call_next(request)

    def _override_db(self, campaign: Optional[SimpleNamespace]):
        self.app.dependency_overrides[get_db] = lambda: SimpleNamespace(
            get=lambda model, pk: campaign
        )

    def _approved_campaign(self):
        return SimpleNamespace(
            tenant_id=self.tenant_id,
            approval_status="Approved",
            template_id=uuid.uuid4(),
            segment_id=uuid.uuid4(),
        )

    def test_activate_submits_when_approved(self):
        self._override_db(self._approved_campaign())
        with patch(
            "core.routers.campaign_activation_api.dagster_client.campaign_activation.activate",
            return_value="run-123",
        ) as mock_activate:
            resp = TestClient(self.app).post(f"/admin/campaigns/{self.campaign_id}/activate")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["run_id"], "run-123")
        self.assertEqual(body["status"], "submitted")
        mock_activate.assert_called_once_with(str(self.campaign_id), self.tenant_id)

    def test_activate_404_when_campaign_missing(self):
        self._override_db(None)
        resp = TestClient(self.app).post(f"/admin/campaigns/{self.campaign_id}/activate")
        self.assertEqual(resp.status_code, 404)

    def test_activate_404_when_other_tenant(self):
        campaign = self._approved_campaign()
        campaign.tenant_id = str(uuid.uuid4())  # different tenant
        self._override_db(campaign)
        resp = TestClient(self.app).post(f"/admin/campaigns/{self.campaign_id}/activate")
        self.assertEqual(resp.status_code, 404)

    def test_activate_409_when_not_approved(self):
        campaign = self._approved_campaign()
        campaign.approval_status = "InReview"
        self._override_db(campaign)
        resp = TestClient(self.app).post(f"/admin/campaigns/{self.campaign_id}/activate")
        self.assertEqual(resp.status_code, 409)

    def test_activate_409_when_missing_template_or_segment(self):
        campaign = self._approved_campaign()
        campaign.template_id = None
        self._override_db(campaign)
        resp = TestClient(self.app).post(f"/admin/campaigns/{self.campaign_id}/activate")
        self.assertEqual(resp.status_code, 409)

    def test_put_provider_config_hides_password(self):
        self.app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        stored = SimpleNamespace(
            config_id=uuid.uuid4(), tenant_id=self.tenant_id, name="default", provider="smtp",
            smtp_host="mail.x", smtp_port=587, smtp_username="u", smtp_password="secret",
            smtp_use_tls=True, from_address="a@x.io", from_name="X", is_active=True,
            metadata_=None, created_at=None, updated_at=None,
        )
        with patch("core.routers.campaign_activation_api.upsert_config", return_value=stored):
            resp = TestClient(self.app).put(
                "/admin/email-provider-config",
                json={"provider": "smtp", "smtp_host": "mail.x", "smtp_port": 587, "smtp_password": "secret"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertNotIn("smtp_password", body)
        self.assertTrue(body["smtp_password_set"])


if __name__ == "__main__":
    unittest.main()
