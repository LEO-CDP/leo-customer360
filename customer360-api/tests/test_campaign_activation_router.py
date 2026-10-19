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
from core.utils.dagster_client import DagsterJobTriggerError
from core.routers.campaign_activation_api import campaign_activation_router
from core.utils.dagster_client import CampaignActivationDagsterService
from leo_customer360_dao.models.crm import Campaign
from leo_customer360_dao.crud.email_provider import upsert_config
from leo_customer360_dao.schemas.crm import EmailProviderConfigUpsert


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
            # Standalone router tests inject the identity the production middleware would set.
            request.state.user = {"roles": ["tenant_admin"]}
            return await call_next(request)

    def _override_db(self, campaign: Optional[SimpleNamespace]):
        self.app.dependency_overrides[get_db] = lambda: SimpleNamespace(
            get=lambda model, pk: campaign if model is Campaign else SimpleNamespace(
                tenant_id=self.tenant_id, is_active=True, status_code=1, segment_tag="vip",
            ),
        )
        patcher = patch(
            "core.repositories.campaign_activation_repository.WorkflowRepository.version_info",
            return_value={"approval_status": "Approved"},
        )
        self.version_info = patcher.start()
        self.addCleanup(patcher.stop)

    def _approved_campaign(self):
        return SimpleNamespace(
            tenant_id=self.tenant_id,
            approval_status="Approved",
            status="Draft",
            segment_id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            workflow_version=1,
            workflow_execution_id=None,
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
        mock_activate.assert_called_once_with(
            str(self.campaign_id), self.tenant_id, restart=False, expected_execution_id=None,
        )

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
        campaign.workflow_id = None
        self._override_db(campaign)
        resp = TestClient(self.app).post(f"/admin/campaigns/{self.campaign_id}/activate")
        self.assertEqual(resp.status_code, 409)

    def test_activate_409_when_workflow_version_is_not_pinned(self):
        campaign = self._approved_campaign()
        campaign.workflow_version = None
        self._override_db(campaign)
        with patch("core.routers.campaign_activation_api.dagster_client.campaign_activation.activate") as submit:
            resp = TestClient(self.app).post(f"/admin/campaigns/{self.campaign_id}/activate")
        self.assertEqual(resp.status_code, 409)
        submit.assert_not_called()

    def test_activate_409_when_workflow_version_is_not_approved(self):
        self._override_db(self._approved_campaign())
        self.version_info.return_value = {"approval_status": "InReview"}
        with patch("core.routers.campaign_activation_api.dagster_client.campaign_activation.activate") as submit:
            resp = TestClient(self.app).post(f"/admin/campaigns/{self.campaign_id}/activate")
        self.assertEqual(resp.status_code, 409)
        submit.assert_not_called()

    def test_activate_409_when_already_running_or_completed(self):
        for status in ("Running", "Completed"):
            campaign = self._approved_campaign()
            campaign.status = status
            self._override_db(campaign)
            with patch("core.routers.campaign_activation_api.dagster_client.campaign_activation.activate") as submit:
                resp = TestClient(self.app).post(f"/admin/campaigns/{self.campaign_id}/activate")
            self.assertEqual(resp.status_code, 409)
            submit.assert_not_called()

    def test_dagster_submission_error_logs_campaign_tenant_and_restart_context(self):
        campaign = self._approved_campaign()
        campaign.status = "Completed"
        campaign.workflow_execution_id = uuid.uuid4()
        self._override_db(campaign)
        with self.assertLogs("core.routers.campaign_activation_api", level="ERROR") as logs, patch(
            "core.routers.campaign_activation_api.dagster_client.campaign_activation.activate",
            side_effect=DagsterJobTriggerError(
                "Dagster rejected campaign_activation_job submission: "
                "unexpected config entries ['restart']; expected campaign_id, tenant_id"
            ),
        ):
            response = TestClient(self.app).post(
                f"/admin/campaigns/{self.campaign_id}/activate",
                json={"restart": True},
            )

        self.assertEqual(response.status_code, 503)
        message = " ".join(logs.output)
        self.assertIn(f"campaign_id={self.campaign_id}", message)
        self.assertIn(f"tenant_id={self.tenant_id}", message)
        self.assertIn("restart=True", message)
        self.assertIn("expected campaign_id, tenant_id", message)

    def test_completed_campaign_replay_requires_explicit_restart(self):
        campaign = self._approved_campaign()
        campaign.status = "Completed"
        campaign.workflow_execution_id = uuid.uuid4()
        self._override_db(campaign)
        with patch(
            "core.routers.campaign_activation_api.dagster_client.campaign_activation.activate",
            return_value="restart-run",
        ) as submit:
            response = TestClient(self.app).post(
                f"/admin/campaigns/{self.campaign_id}/activate",
                json={"restart": True},
            )
        self.assertEqual(response.status_code, 200, response.text)
        submit.assert_called_once_with(
            str(self.campaign_id), self.tenant_id, restart=True,
            expected_execution_id=str(campaign.workflow_execution_id),
        )

    def test_completed_campaign_without_restart_flag_is_rejected(self):
        campaign = self._approved_campaign()
        campaign.status = "Completed"
        self._override_db(campaign)
        with patch("core.routers.campaign_activation_api.dagster_client.campaign_activation.activate") as submit:
            response = TestClient(self.app).post(f"/admin/campaigns/{self.campaign_id}/activate")
        self.assertEqual(response.status_code, 409)
        submit.assert_not_called()

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

    def test_partial_provider_update_preserves_existing_state(self):
        existing = SimpleNamespace(
            tenant_id=uuid.UUID(self.tenant_id), name="existing", provider="SENDGRID",
            is_active=False, config={"from_address": "old@example.com"}, credentials={},
        )
        db = SimpleNamespace(
            execute=lambda statement: SimpleNamespace(scalar_one_or_none=lambda: existing),
            commit=lambda: None,
            refresh=lambda row: None,
        )

        result = upsert_config(db, uuid.UUID(self.tenant_id), EmailProviderConfigUpsert(name="existing"))

        self.assertIs(result, existing)
        self.assertEqual(existing.provider, "SENDGRID")
        self.assertFalse(existing.is_active)

    def test_dagster_service_forwards_explicit_restart_flag(self):
        service = CampaignActivationDagsterService()
        with patch.object(service, "submit", return_value="activation-run") as submit:
            run_id = service.activate(
                str(self.campaign_id), self.tenant_id, restart=True,
                expected_execution_id="previous-execution",
            )

        self.assertEqual(run_id, "activation-run")
        self.assertTrue(
            submit.call_args.kwargs["run_config"]["ops"]["activate_campaign_op"]["config"]["restart"]
        )
        self.assertEqual(submit.call_args.kwargs["tags"]["restart"], "true")
        self.assertEqual(
            submit.call_args.kwargs["run_config"]["ops"]["activate_campaign_op"]["config"]["expected_execution_id"],
            "previous-execution",
        )


if __name__ == "__main__":
    unittest.main()
