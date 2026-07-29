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


class SysMetadataAuthExemptionTests(unittest.TestCase):
    """Confirms the metadata endpoints are in core.auth.EXEMPT_PATHS when the
    router is mounted under /api/v1, so the auth middleware lets them through
    even when SSO_LOGIN is enabled."""

    def test_metadata_paths_are_exempt(self):
        from core.auth import EXEMPT_PATHS

        self.assertIn("/api/v1/metadata", EXEMPT_PATHS)
        self.assertIn("/api/v1/metadata/dagster", EXEMPT_PATHS)


if __name__ == "__main__":
    unittest.main()
