"""Unit tests for the system metadata endpoints (core.routers.metadata).

Mocks out all external connectivity (Postgres engine, Redis client, socket)
so the tests are fast and hermetic.
"""

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import get_db
from core.models.system import SysDataSource
from core.routers.metadata import all_metadata_routers


class SysMetadataTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(all_metadata_routers[0])
        self.app.dependency_overrides[get_db] = lambda: None

    def _patch_all_healthy(self):
        """Patches every dependency to look healthy. Use as a context manager."""
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(
            patch(
                "core.routers.metadata.engine.connect",
                MagicMock(
                    __enter__=MagicMock(return_value=MagicMock(execute=MagicMock())),
                    __exit__=MagicMock(),
                ),
            )
        )
        stack.enter_context(
            patch("core.routers.metadata.get_redis_client", return_value=MagicMock(ping=MagicMock()))
        )
        stack.enter_context(
            patch("core.routers.metadata.socket.create_connection", return_value=MagicMock(close=MagicMock()))
        )
        return stack

    def test_metadata_returns_version_and_services(self):
        with self._patch_all_healthy():
            response = TestClient(self.app).get("/metadata/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["service"], "customer360-api")
        self.assertEqual(body["api_version"], "1.0.0")
        self.assertEqual(body["overall_status"], "healthy")
        self.assertIn("postgres", body["services"])
        self.assertIn("redis", body["services"])
        self.assertIn("dagster", body["services"])
        self.assertEqual(body["services"]["postgres"]["status"], "reachable")
        self.assertEqual(body["services"]["redis"]["status"], "reachable")
        self.assertEqual(body["services"]["dagster"]["status"], "reachable")

    def test_metadata_postgres_unreachable_marks_degraded(self):
        with patch("core.routers.metadata.engine.connect", side_effect=RuntimeError("db down")):
            response = TestClient(self.app).get("/metadata/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["services"]["postgres"]["status"], "unreachable")
        self.assertEqual(body["overall_status"], "degraded")

    def test_metadata_redis_disabled_is_healthy(self):
        with (
            patch(
                "core.routers.metadata.engine.connect",
                MagicMock(__enter__=MagicMock(return_value=MagicMock(execute=MagicMock())), __exit__=MagicMock()),
            ),
            patch("core.routers.metadata.get_redis_client", return_value=None),
            patch("core.routers.metadata.socket.create_connection", return_value=MagicMock(close=MagicMock())),
        ):
            response = TestClient(self.app).get("/metadata/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["services"]["redis"]["status"], "disabled")
        self.assertEqual(body["overall_status"], "healthy")

    def test_metadata_dagster_unreachable(self):
        with (
            patch(
                "core.routers.metadata.engine.connect",
                MagicMock(__enter__=MagicMock(return_value=MagicMock(execute=MagicMock())), __exit__=MagicMock()),
            ),
            patch("core.routers.metadata.get_redis_client", return_value=MagicMock(ping=MagicMock())),
            patch("core.routers.metadata.socket.create_connection", side_effect=OSError("refused")),
        ):
            response = TestClient(self.app).get("/metadata/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["services"]["dagster"]["status"], "unreachable")
        self.assertEqual(body["services"]["dagster"].get("error"), "refused")

    def test_dagster_metadata_returns_configured_services(self):
        with patch(
            "core.routers.metadata.socket.create_connection",
            return_value=MagicMock(close=MagicMock()),
        ):
            response = TestClient(self.app).get("/metadata/dagster")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["service"], "dagster")
        self.assertEqual(body["status"], "reachable")
        self.assertIsNone(body.get("error"))
        service_names = {s["name"] for s in body["configured_services"]}
        self.assertIn("segmentation", service_names)
        self.assertIn("identity_resolution", service_names)
        self.assertIn("analytics", service_names)
        for svc in body["configured_services"]:
            self.assertIn("job_name", svc)
            self.assertIn("location_name", svc)
            self.assertIn("repository_name", svc)

    def test_dagster_metadata_surfaces_unreachable_status(self):
        with patch(
            "core.routers.metadata.socket.create_connection",
            side_effect=OSError("timeout"),
        ):
            response = TestClient(self.app).get("/metadata/dagster")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "unreachable")
        self.assertEqual(body["error"], "timeout")
        self.assertIn("configured_services", body)

    def test_metadata_domains_returns_active_domains_from_db(self):
        mock_db = MagicMock()
        mock_db.execute.return_value.all.return_value = [
            ("retail", "Retail & E-Commerce"),
            ("banking", "Banking & Financial Services"),
        ]
        self.app.dependency_overrides[get_db] = lambda: mock_db

        response = TestClient(self.app).get("/metadata/domains")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["retail"], "Retail & E-Commerce")
        self.assertEqual(body["banking"], "Banking & Financial Services")

    def test_metadata_domains_filters_by_tenant_id_query_param(self):
        mock_db = MagicMock()
        mock_db.execute.return_value.all.return_value = [("retail", "Retail & E-Commerce")]
        self.app.dependency_overrides[get_db] = lambda: mock_db

        tenant_id = "22222222-2222-2222-2222-222222222222"
        response = TestClient(self.app).get(f"/metadata/domains?tenant_id={tenant_id}")

        self.assertEqual(response.status_code, 200)
        executed_stmt = mock_db.execute.call_args[0][0]
        # The WHERE clause must filter sys_tenant_domain by the tenant_id we passed in.
        self.assertIn("sys_tenant_domain.tenant_id", str(executed_stmt))

    def test_metadata_domains_surfaces_db_failure_as_503(self):
        mock_db = MagicMock()
        mock_db.execute.side_effect = RuntimeError("db down")
        self.app.dependency_overrides[get_db] = lambda: mock_db

        response = TestClient(self.app).get("/metadata/domains")

        self.assertEqual(response.status_code, 503)

    def test_metadata_data_sources_returns_sources_from_db(self):
        mock_db = MagicMock()
        tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
        mock_db.execute.return_value.scalars.return_value.all.return_value = [
            SysDataSource(
                data_source_id=uuid.uuid4(),
                tenant_id=tenant_id,
                name="AppsFlyer",
                slug="appsflyer",
                source_type=2,
                status=1,
            )
        ]
        self.app.dependency_overrides[get_db] = lambda: mock_db

        response = TestClient(self.app).get("/metadata/data-sources")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["name"], "AppsFlyer")
        self.assertEqual(body[0]["slug"], "appsflyer")

    def test_metadata_data_sources_create_returns_created_resource(self):
        mock_db = MagicMock()
        def _refresh_with_defaults(obj):
            if getattr(obj, "data_source_id", None) is None:
                obj.data_source_id = uuid.uuid4()

        mock_db.refresh.side_effect = _refresh_with_defaults
        self.app.dependency_overrides[get_db] = lambda: mock_db

        response = TestClient(self.app).post(
            "/metadata/data-sources",
            json={
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "name": "GA4",
                "slug": "ga4",
                "status": 1,
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "GA4")
        self.assertEqual(response.json()["slug"], "ga4")
        self.assertTrue(mock_db.add.called)
        self.assertTrue(mock_db.commit.called)

    def test_metadata_data_sources_update_and_delete(self):
        tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
        data_source = SysDataSource(
            data_source_id=uuid.uuid4(),
            tenant_id=tenant_id,
            name="Old Name",
            slug="old-name",
            status=0,
        )
        mock_db = MagicMock()
        mock_db.get.return_value = data_source
        self.app.dependency_overrides[get_db] = lambda: mock_db

        patch_response = TestClient(self.app).patch(
            f"/metadata/data-sources/{data_source.data_source_id}",
            json={"name": "Updated Name"},
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["name"], "Updated Name")

        delete_response = TestClient(self.app).delete(f"/metadata/data-sources/{data_source.data_source_id}")
        self.assertEqual(delete_response.status_code, 204)

    def test_metadata_data_sources_surfaces_db_failure_as_503(self):
        mock_db = MagicMock()
        mock_db.execute.side_effect = RuntimeError("db down")
        self.app.dependency_overrides[get_db] = lambda: mock_db

        response = TestClient(self.app).get("/metadata/data-sources")

        self.assertEqual(response.status_code, 503)


class SysMetadataAuthExemptionTests(unittest.TestCase):
    """Confirms the metadata endpoints are in core.auth.EXEMPT_PATHS when the
    router is mounted under /api/v1, so the auth middleware lets them through
    even when SSO_LOGIN is enabled."""

    def test_metadata_paths_are_exempt(self):
        from core.auth import EXEMPT_PATHS

        self.assertIn("/api/v1/metadata", EXEMPT_PATHS)
        self.assertIn("/api/v1/metadata/dagster", EXEMPT_PATHS)
        self.assertIn("/api/v1/metadata/domains", EXEMPT_PATHS)
        self.assertIn("/api/v1/metadata/data-sources", EXEMPT_PATHS)


if __name__ == "__main__":
    unittest.main()
