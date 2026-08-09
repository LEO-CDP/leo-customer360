import importlib
import sys
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import get_db
from core.schemas.identity import ProfileAttributeRead


class FakeProfileAttributeCRUD:
    last_list_kwargs = {}
    last_count_kwargs = {}
    created_items = []
    updated_items = []
    deleted_items = []

    def __init__(self, model):
        self.model = model

    def list(self, db, *, skip=0, limit=100, **filters):
        self.__class__.last_list_kwargs = filters
        return [self._build_item()]

    def count(self, db, **filters):
        self.__class__.last_count_kwargs = filters
        return 1

    def get(self, db, pk):
        return self._build_item(id=pk)

    def create(self, db, obj_in):
        self.__class__.created_items.append((db, obj_in))
        return self._build_item(**obj_in)

    def update(self, db, db_obj, obj_in):
        self.__class__.updated_items.append((db, db_obj, obj_in))
        for key, value in obj_in.items():
            setattr(db_obj, key, value)
        return db_obj

    def delete(self, db, db_obj):
        self.__class__.deleted_items.append((db, db_obj))
        return None

    @staticmethod
    def _build_item(id=None, **overrides):
        item = SimpleNamespace(
            id=id or uuid.uuid4(),
            attribute_internal_code="email",
            master_profile_column="email",
            name="Email",
            description="Primary email",
            attribute_group="IDENTITY",
            source_table="cdp_master_profiles",
            status="ACTIVE",
            data_type="TEXT",
            domain_scope="all",
            is_pii=True,
            is_segmentable=True,
            is_identity_resolution=True,
            matching_rule="exact",
            matching_threshold=None,
            consolidation_rule="non_null",
            consolidation_config={},
            priority_rank=99,
            value_limit=5,
            limit_timeframe="5_annually",
            blocked_values=["null", "-1"],
            blocked_patterns=["^[0-]*$"],
            is_scoring_model=False,
            scoring_model_name=None,
            scoring_model_version=None,
            value_type="identifier",
            value_min=None,
            value_max=None,
            refresh_frequency=None,
            display_order=0,
            created_at=None,
            updated_at=None,
        )
        for key, value in overrides.items():
            setattr(item, key, value)
        return item


class ProfileAttributesUUIDRouterTests(unittest.TestCase):
    def _build_client(self):
        sys.modules.pop("core.routers.identity_api", None)
        with patch("core.routers._generic.CRUDBase", FakeProfileAttributeCRUD), patch(
            "core.cache.get_redis_client", return_value=None
        ):
            identity_router = importlib.import_module("core.routers.identity_api")
            with patch.object(identity_router, "validate_domain_value", return_value=None):
                app = FastAPI()
                app.include_router(identity_router.profile_attributes_router)
                app.dependency_overrides[get_db] = lambda: None
                return TestClient(app)

    def test_profile_attribute_read_schema_accepts_uuid_ids(self):
        item_id = uuid.uuid4()
        payload = ProfileAttributeRead.model_validate(
            {
                "id": item_id,
                "attribute_internal_code": "email",
                "name": "Email",
                "domain_scope": "all",
            }
        )
        self.assertEqual(payload.id, item_id)
        self.assertIsInstance(payload.id, uuid.UUID)

    def test_profile_attributes_list_endpoint_returns_uuid_ids(self):
        client = self._build_client()

        response = client.get("/profile-attributes/?skip=0&limit=500")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(isinstance(data, list))
        self.assertTrue(uuid.UUID(data[0]["id"]))

    def test_profile_attributes_get_endpoint_accepts_uuid_path_param(self):
        item_id = str(uuid.uuid4())
        client = self._build_client()

        response = client.get(f"/profile-attributes/{item_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], item_id)

    def test_profile_attributes_get_endpoint_rejects_invalid_uuid(self):
        client = self._build_client()

        response = client.get("/profile-attributes/not-a-uuid")

        self.assertEqual(response.status_code, 422)

    def test_profile_attributes_create_endpoint_returns_created_profile_attribute(self):
        client = self._build_client()
        payload = {
            "attribute_internal_code": "email",
            "name": "Email",
            "description": "Primary email",
            "domain_scope": "all",
        }

        response = client.post("/profile-attributes/", json=payload)

        self.assertEqual(response.status_code, 201)
        self.assertTrue(uuid.UUID(response.json()["id"]))
        self.assertEqual(response.json()["name"], "Email")

    def test_profile_attributes_update_endpoint_updates_profile_attribute(self):
        item_id = str(uuid.uuid4())
        client = self._build_client()

        response = client.patch(f"/profile-attributes/{item_id}", json={"name": "Updated Email"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Updated Email")

    def test_profile_attributes_delete_endpoint_removes_profile_attribute(self):
        item_id = str(uuid.uuid4())
        client = self._build_client()

        response = client.delete(f"/profile-attributes/{item_id}")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.text, "")


if __name__ == "__main__":
    unittest.main()
