"""Profile event endpoints answer a handled 503 (not a bare 500) when the event store fails."""

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import get_db
from core.routers import identity_api
from leo_customer360_dao.repositories.master_profile_event_repository import MasterProfileEventStoreError

PROFILE = uuid.uuid4()
ENDPOINTS = {
    "engagement-summary": "get_engagement_summary",
    "channel-activity": "get_channel_activity",
    "top-interests": "get_top_interests",
}


class EventStoreErrorTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(identity_api.master_profiles_router)
        app.dependency_overrides[get_db] = lambda: MagicMock()
        self.client = TestClient(app, raise_server_exceptions=False)

    def _repo(self, method):
        repo = MagicMock()
        repo.get_master_profile.return_value = SimpleNamespace(tenant_id=uuid.uuid4())
        getattr(repo, method).side_effect = MasterProfileEventStoreError("no S3")
        return repo

    def test_each_event_endpoint_is_a_handled_503(self):
        for path, method in ENDPOINTS.items():
            with self.subTest(path), patch.object(identity_api, "_identity_repository", return_value=self._repo(method)):
                response = self.client.get(f"/master-profiles/{PROFILE}/{path}", params={"days": 7})
                self.assertEqual(response.status_code, 503, response.text)
                self.assertIn("unavailable", response.json()["detail"])
