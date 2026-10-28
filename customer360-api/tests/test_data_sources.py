"""Unit tests for the independent tenant-scoped ``/data-sources`` router."""

import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import get_db
from core.routers.data_source_api import data_source_router
from leo_customer360_dao.models.system import SysDataSource


class DataSourceRouterTests(unittest.TestCase):
	tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

	def setUp(self):
		self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
		self._cache_patcher.start()
		self.addCleanup(self._cache_patcher.stop)
		self.app = FastAPI()
		self.app.include_router(data_source_router)
		self.db = MagicMock()
		self.app.dependency_overrides[get_db] = lambda: self.db
		self.client = TestClient(self.app)

	def _source(self, **overrides):
		values = {"data_source_id": uuid.uuid4(), "tenant_id": self.tenant_id, "name": "Adjust", "slug": "adjust", "source_type": 2, "status": 1}
		values.update(overrides)
		return SysDataSource(**values)

	def test_list_returns_tenant_scoped_sources(self):
		self.db.execute.return_value.scalars.return_value.all.return_value = [self._source()]
		response = self.client.get("/data-sources")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()[0]["slug"], "adjust")

	def test_trailing_slash_and_no_slash_both_resolve_without_redirect(self):
		self.db.execute.return_value.scalars.return_value.all.return_value = [self._source()]
		# Behind reverse proxy, both variants must return 200 directly with no 307 redirect
		r1 = self.client.get("/data-sources", follow_redirects=False)
		r2 = self.client.get("/data-sources/", follow_redirects=False)
		self.assertEqual(r1.status_code, 200)
		self.assertEqual(r2.status_code, 200)

	def test_count_returns_total(self):
		self.db.execute.return_value.scalar_one.return_value = 5
		response = self.client.get("/data-sources/count")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json(), {"count": 5})

	def test_list_query_matrix_and_invalid_uuid(self):
		self.db.execute.return_value.scalars.return_value.all.return_value = []
		for params in ({"status": 1}, {"skip": 3, "limit": 1}, {"tenant_id": str(uuid.uuid4())}):
			with self.subTest(params=params):
				self.assertEqual(self.client.get("/data-sources", params=params).status_code, 200)
		self.assertEqual(self.client.get("/data-sources?tenant_id=invalid").status_code, 422)
		self.assertEqual(self.client.get("/data-sources?limit=999999").status_code, 422)

	def test_get_update_delete_matrix(self):
		source = self._source(name="Old")
		self.db.get.side_effect = lambda _, source_id: source if source_id == source.data_source_id else None
		self.db.refresh.side_effect = lambda value: setattr(value, "updated_at", datetime.now(timezone.utc))
		self.assertEqual(self.client.get(f"/data-sources/{source.data_source_id}").status_code, 200)
		updated = self.client.patch(f"/data-sources/{source.data_source_id}", json={"name": "New"})
		self.assertEqual(updated.status_code, 200)
		self.assertEqual(updated.json()["name"], "New")
		self.assertEqual(self.client.delete(f"/data-sources/{source.data_source_id}").status_code, 204)
		self.assertEqual(self.client.get(f"/data-sources/{uuid.uuid4()}").status_code, 404)
		self.assertEqual(self.client.patch(f"/data-sources/{uuid.uuid4()}", json={"name": "Missing"}).status_code, 404)
		self.assertEqual(self.client.delete(f"/data-sources/{uuid.uuid4()}").status_code, 404)

	def test_create_persists_resource_and_rejects_invalid_payloads(self):
		self.db.refresh.side_effect = lambda value: setattr(value, "data_source_id", uuid.uuid4())
		valid = {"tenant_id": str(self.tenant_id), "name": "GA4", "slug": "ga4", "status": 1, "data_source_url": "https://analytics.google.com", "access_tokens": {"measurement_id": "G-TEST"}}
		response = self.client.post("/data-sources", json=valid)
		self.assertEqual(response.status_code, 201)
		self.assertEqual(response.json()["access_tokens"], {"measurement_id": "G-TEST"})
		self.assertTrue(self.db.commit.called)
		for payload in ({"name": "Missing fields"}, {"tenant_id": str(self.tenant_id), "name": "Invalid", "slug": "invalid", "source_type": 9}):
			with self.subTest(payload=payload):
				self.assertEqual(self.client.post("/data-sources", json=payload).status_code, 422)

	def test_update_rejects_invalid_source_type_and_list_failure_is_503(self):
		self.assertEqual(self.client.patch(f"/data-sources/{uuid.uuid4()}", json={"source_type": 0}).status_code, 422)
		self.db.execute.side_effect = RuntimeError("db down")
		self.assertEqual(self.client.get("/data-sources").status_code, 503)

	def test_item_database_failures_return_503(self):
		self.db.get.side_effect = RuntimeError("db down")
		data_source_id = uuid.uuid4()

		self.assertEqual(self.client.get(f"/data-sources/{data_source_id}").status_code, 503)
		self.assertEqual(self.client.patch(f"/data-sources/{data_source_id}", json={"name": "New"}).status_code, 503)
		self.assertEqual(self.client.delete(f"/data-sources/{data_source_id}").status_code, 503)