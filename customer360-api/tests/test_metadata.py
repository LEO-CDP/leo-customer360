"""Unit tests for the system metadata endpoints (core.routers.metadata_api).

Mocks out all external connectivity (Postgres engine, Redis client, socket)
so the tests are fast and hermetic.
"""

import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.repositories.metadata_repository as mr
from core.database import get_db
from core.routers.metadata_api import metadata_router


class SysMetadataTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(metadata_router)
        self.app.dependency_overrides[get_db] = lambda: None

    def _patch_all_healthy(self):
        """Patches every dependency to look healthy. Use as a context manager."""
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(
            patch(
                "core.repositories.metadata_repository.engine.connect",
                MagicMock(
                    __enter__=MagicMock(return_value=MagicMock(execute=MagicMock())),
                    __exit__=MagicMock(),
                ),
            )
        )
        stack.enter_context(
            patch("core.repositories.metadata_repository.get_redis_client", return_value=MagicMock(ping=MagicMock()))
        )
        stack.enter_context(
            patch("core.repositories.metadata_repository.socket.create_connection", return_value=MagicMock(close=MagicMock()))
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
        with patch("core.repositories.metadata_repository.engine.connect", side_effect=RuntimeError("db down")):
            response = TestClient(self.app).get("/metadata/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["services"]["postgres"]["status"], "unreachable")
        self.assertEqual(body["overall_status"], "degraded")

    def test_metadata_redis_disabled_is_healthy(self):
        with (
            patch(
                "core.repositories.metadata_repository.engine.connect",
                MagicMock(__enter__=MagicMock(return_value=MagicMock(execute=MagicMock())), __exit__=MagicMock()),
            ),
            patch("core.repositories.metadata_repository.get_redis_client", return_value=None),
            patch("core.repositories.metadata_repository.socket.create_connection", return_value=MagicMock(close=MagicMock())),
        ):
            response = TestClient(self.app).get("/metadata/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["services"]["redis"]["status"], "disabled")
        self.assertEqual(body["overall_status"], "healthy")

    def test_metadata_dagster_unreachable(self):
        with (
            patch(
                "core.repositories.metadata_repository.engine.connect",
                MagicMock(__enter__=MagicMock(return_value=MagicMock(execute=MagicMock())), __exit__=MagicMock()),
            ),
            patch("core.repositories.metadata_repository.get_redis_client", return_value=MagicMock(ping=MagicMock())),
            patch("core.repositories.metadata_repository.socket.create_connection", side_effect=OSError("refused")),
        ):
            response = TestClient(self.app).get("/metadata/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["services"]["dagster"]["status"], "unreachable")
        self.assertEqual(body["services"]["dagster"].get("error"), "refused")

    def test_dagster_metadata_returns_configured_services(self):
        with patch(
            "core.repositories.metadata_repository.socket.create_connection",
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
            "core.repositories.metadata_repository.socket.create_connection",
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

class SysMetadataAuthExemptionTests(unittest.TestCase):
    """Confirms only GET /metadata (needed by the login screen itself) is
    exempt from auth -- every other /metadata/* route is real protected API
    data and must require authentication like everything else (see
    core.auth.EXEMPT_PATHS)."""

    def test_root_metadata_path_is_exempt(self):
        from core.auth import EXEMPT_PATHS

        self.assertIn("/api/v1/metadata", EXEMPT_PATHS)

    def test_other_metadata_paths_are_not_exempt(self):
        from core.auth import EXEMPT_PATHS

        self.assertNotIn("/api/v1/metadata/dagster", EXEMPT_PATHS)
        self.assertNotIn("/api/v1/metadata/domains", EXEMPT_PATHS)
        self.assertNotIn("/api/v1/data-sources", EXEMPT_PATHS)
        self.assertNotIn("/api/v1/ai-agents", EXEMPT_PATHS)
        # The SMTP probe does a real login -- it must NOT be login-screen exempt.
        self.assertNotIn("/api/v1/metadata/smtp", EXEMPT_PATHS)


class SmtpHealthTests(unittest.TestCase):
    """GET /metadata/smtp: 'disabled' in mock mode, else an active SMTP
    connect + login probe. All SMTP I/O is mocked so the test is hermetic."""

    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(metadata_router)
        self.app.dependency_overrides[get_db] = lambda: None

    def test_smtp_disabled_in_mock_mode(self):
        with patch.object(mr.settings, "email_dispatch_adapter", "mock"):
            response = TestClient(self.app).get("/metadata/smtp")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["service"], "smtp")
        self.assertEqual(body["status"], "disabled")

    def test_smtp_reachable_when_login_succeeds(self):
        server = MagicMock()
        server.__enter__ = MagicMock(return_value=server)
        server.__exit__ = MagicMock(return_value=False)
        with (
            patch.object(mr.settings, "email_dispatch_adapter", "smtp"),
            patch.object(mr.settings, "smtp_host", "smtp-relay.brevo.com"),
            patch.object(mr.settings, "smtp_username", "user"),
            patch.object(mr.settings, "smtp_password", "key"),
            patch("core.repositories.metadata_repository.smtplib.SMTP", return_value=server),
        ):
            response = TestClient(self.app).get("/metadata/smtp")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "reachable")
        server.login.assert_called_once_with("user", "key")
        server.noop.assert_called_once()

    def test_smtp_no_credentials_not_reported_reachable(self):
        # SMTP enabled + host reachable but no username/password: login is never
        # attempted, so the probe must NOT claim the credential authenticates.
        server = MagicMock()
        server.__enter__ = MagicMock(return_value=server)
        server.__exit__ = MagicMock(return_value=False)
        with (
            patch.object(mr.settings, "email_dispatch_adapter", "smtp"),
            patch.object(mr.settings, "smtp_host", "smtp-relay.brevo.com"),
            patch.object(mr.settings, "smtp_username", None),
            patch.object(mr.settings, "smtp_password", None),
            patch("core.repositories.metadata_repository.smtplib.SMTP", return_value=server),
        ):
            response = TestClient(self.app).get("/metadata/smtp")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "no_credentials")
        server.login.assert_not_called()

    def test_smtp_unreachable_surfaces_error(self):
        with (
            patch.object(mr.settings, "email_dispatch_adapter", "smtp"),
            patch.object(mr.settings, "smtp_host", "smtp-relay.brevo.com"),
            patch("core.repositories.metadata_repository.smtplib.SMTP", side_effect=OSError("refused")),
        ):
            response = TestClient(self.app).get("/metadata/smtp")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "unreachable")
        # A fixed category, not the raw exception text (no stack/internal leak).
        self.assertEqual(body["error"], "connection_error")
        self.assertNotIn("refused", str(body))


if __name__ == "__main__":
    unittest.main()
