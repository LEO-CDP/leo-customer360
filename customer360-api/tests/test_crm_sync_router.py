"""HTTP-level tests for the segment -> CRM sync admin router
(core.routers.crm_sync_api). The sync engine itself (sync_segment_to_crm) is
mocked out -- these verify request/response wiring, tenant scoping and the
404/400 guards, not the routing logic (see tests/test_crm_sync_crud.py)."""

import unittest
import uuid
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import get_db
from core.routers.crm_sync_api import crm_sync_router


def _canned_result(segment_id: uuid.UUID, tenant_id: uuid.UUID, dry_run: bool = False) -> dict:
    return {
        "sync_run_id": uuid.uuid4(),
        "segment_id": segment_id,
        "tenant_id": tenant_id,
        "status": "Completed",
        "dry_run": dry_run,
        "route_counts": {"matched": 3, "customer": 1, "lead": 1, "contact": 1, "skipped": 0, "error": 0},
        "detail": {"leads_written": 1},
        "message": "ok",
    }


class SyncSegmentRouterTests(unittest.TestCase):
    def setUp(self):
        # Cache off so @cache_response (used elsewhere) never reaches real Redis.
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        self.app = FastAPI()
        self.app.include_router(crm_sync_router)
        self.app.dependency_overrides[get_db] = lambda: None

    def _client(self, fake_segment: Optional[SimpleNamespace], tenant_id: Optional[str]) -> TestClient:
        repo_patcher = patch(
            "core.routers.crm_sync_api.SegmentRepository",
            side_effect=lambda db: SimpleNamespace(get_segment=lambda sid: fake_segment),
        )
        repo_patcher.start()
        self.addCleanup(repo_patcher.stop)

        @self.app.middleware("http")
        async def _inject_tenant(request, call_next):
            if tenant_id is not None:
                request.state.tenant_id = tenant_id
            # Under SSO (forced on in CI) the router gates on a tenant-admin
            # role; inject one so we exercise the handler, not the auth 401.
            request.state.user = {"roles": ["tenant_admin"]}
            return await call_next(request)

        return TestClient(self.app)

    def test_sync_success_calls_engine_and_returns_counts(self):
        tenant_id = uuid.uuid4()
        segment = SimpleNamespace(segment_id=uuid.uuid4(), tenant_id=tenant_id, sql_rules="engagement_score > 0")
        client = self._client(segment, tenant_id=str(tenant_id))

        with patch(
            "core.routers.crm_sync_api.sync_segment_to_crm",
            return_value=_canned_result(segment.segment_id, tenant_id),
        ) as mock_sync:
            response = client.post(f"/admin/crm/sync-segment/{segment.segment_id}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["route_counts"]["matched"], 3)
        self.assertEqual(body["status"], "Completed")
        self.assertFalse(mock_sync.call_args.kwargs["dry_run"])
        self.assertEqual(mock_sync.call_args.kwargs["tenant_id"], str(tenant_id))

    def test_sync_dry_run_passes_flag_to_engine(self):
        tenant_id = uuid.uuid4()
        segment = SimpleNamespace(segment_id=uuid.uuid4(), tenant_id=tenant_id, sql_rules="engagement_score > 0")
        client = self._client(segment, tenant_id=str(tenant_id))

        with patch(
            "core.routers.crm_sync_api.sync_segment_to_crm",
            return_value=_canned_result(segment.segment_id, tenant_id, dry_run=True),
        ) as mock_sync:
            response = client.post(f"/admin/crm/sync-segment/{segment.segment_id}?dry_run=true")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["dry_run"])
        self.assertTrue(mock_sync.call_args.kwargs["dry_run"])

    def test_sync_404_when_segment_missing(self):
        client = self._client(None, tenant_id=str(uuid.uuid4()))
        response = client.post(f"/admin/crm/sync-segment/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)

    def test_sync_404_when_segment_belongs_to_other_tenant(self):
        segment = SimpleNamespace(segment_id=uuid.uuid4(), tenant_id=uuid.uuid4(), sql_rules="engagement_score > 0")
        client = self._client(segment, tenant_id=str(uuid.uuid4()))  # different tenant
        response = client.post(f"/admin/crm/sync-segment/{segment.segment_id}")
        self.assertEqual(response.status_code, 404)

    def test_sync_400_when_segment_has_no_sql_rules(self):
        tenant_id = uuid.uuid4()
        segment = SimpleNamespace(segment_id=uuid.uuid4(), tenant_id=tenant_id, sql_rules=None)
        client = self._client(segment, tenant_id=str(tenant_id))
        response = client.post(f"/admin/crm/sync-segment/{segment.segment_id}")
        self.assertEqual(response.status_code, 400)

    def test_sync_400_when_no_tenant_context(self):
        segment = SimpleNamespace(segment_id=uuid.uuid4(), tenant_id=uuid.uuid4(), sql_rules="engagement_score > 0")
        client = self._client(segment, tenant_id=None)
        response = client.post(f"/admin/crm/sync-segment/{segment.segment_id}")
        self.assertEqual(response.status_code, 400)

    def test_sync_400_when_engine_rejects_unsafe_rules(self):
        tenant_id = uuid.uuid4()
        segment = SimpleNamespace(segment_id=uuid.uuid4(), tenant_id=tenant_id, sql_rules="bad")
        client = self._client(segment, tenant_id=str(tenant_id))
        with patch(
            "core.routers.crm_sync_api.sync_segment_to_crm",
            side_effect=ValueError("unsafe sql_rules"),
        ):
            response = client.post(f"/admin/crm/sync-segment/{segment.segment_id}")
        self.assertEqual(response.status_code, 400)


class SyncRunAuditRouterTests(unittest.TestCase):
    def setUp(self):
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)
        self.app = FastAPI()
        self.app.include_router(crm_sync_router)

    def _run_row(self, tenant_id: uuid.UUID) -> SimpleNamespace:
        return SimpleNamespace(
            sync_run_id=uuid.uuid4(),
            tenant_id=tenant_id,
            segment_id=uuid.uuid4(),
            triggered_by=None,
            status="Completed",
            dry_run=False,
            matched_count=3,
            customer_count=1,
            lead_count=1,
            contact_count=1,
            skipped_count=0,
            error_count=0,
            error_message=None,
            started_at=None,
            finished_at=None,
            metadata_=None,
        )

    def _client(self, fake_db, tenant_id: str) -> TestClient:
        self.app.dependency_overrides[get_db] = lambda: fake_db

        @self.app.middleware("http")
        async def _inject_tenant(request, call_next):
            request.state.tenant_id = tenant_id
            # Under SSO (forced on in CI) the router gates on a tenant-admin
            # role; inject one so we exercise the handler, not the auth 401.
            request.state.user = {"roles": ["tenant_admin"]}
            return await call_next(request)

        return TestClient(self.app)

    def test_get_sync_run_returns_row_for_own_tenant(self):
        tenant_id = uuid.uuid4()
        run = self._run_row(tenant_id)
        fake_db = SimpleNamespace(get=lambda model, pk: run)
        client = self._client(fake_db, tenant_id=str(tenant_id))

        response = client.get(f"/admin/crm/sync-runs/{run.sync_run_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["matched_count"], 3)

    def test_get_sync_run_404_for_other_tenant(self):
        run = self._run_row(uuid.uuid4())
        fake_db = SimpleNamespace(get=lambda model, pk: run)
        client = self._client(fake_db, tenant_id=str(uuid.uuid4()))

        response = client.get(f"/admin/crm/sync-runs/{run.sync_run_id}")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
