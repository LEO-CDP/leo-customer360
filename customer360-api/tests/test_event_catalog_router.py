import importlib
import sys
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import require_attribute_manager
from core.database import get_db
from leo_customer360_dao.schemas.identity import EventCatalogRead


class FakeEventCatalogCRUD:
    created_items = []
    updated_items = []
    deleted_items = []

    def __init__(self, model):
        self.model = model

    @staticmethod
    def build_item(id=None, **overrides):
        item = SimpleNamespace(
            id=id or uuid.uuid4(),
            event_name="purchase",
            event_category="COMMERCE",
            domain_scope="retail",
            description="A customer completed a purchase.",
            is_conversion_default=True,
            value_field="order_total",
            display_order=180,
            status="ACTIVE",
            created_at=None,
            updated_at=None,
        )
        for key, value in overrides.items():
            setattr(item, key, value)
        return item

    def list(self, db, *, skip=0, limit=100, **filters):
        return [self.build_item()]

    def count(self, db, **filters):
        return 1

    def get(self, db, pk):
        return self.build_item(id=pk)

    def create(self, db, obj_in):
        self.__class__.created_items.append(obj_in)
        return self.build_item(**obj_in)

    def update(self, db, db_obj, obj_in):
        self.__class__.updated_items.append(obj_in)
        for key, value in obj_in.items():
            setattr(db_obj, key, value)
        return db_obj

    def delete(self, db, db_obj):
        self.__class__.deleted_items.append(db_obj.id)


class EventCatalogRouterTests(unittest.TestCase):
    def build_client(self):
        sys.modules.pop("core.routers.identity_api", None)
        with patch("core.routers._generic.CRUDBase", FakeEventCatalogCRUD), patch(
            "core.cache.get_redis_client", return_value=None
        ):
            identity_router = importlib.import_module("core.routers.identity_api")
            identity_router.validate_domain_value = lambda *args, **kwargs: None
            identity_router.invalidate_key = lambda *args, **kwargs: None
            app = FastAPI()
            app.include_router(identity_router.event_catalog_router)
            app.dependency_overrides[get_db] = lambda: None
            app.dependency_overrides[require_attribute_manager] = lambda: None
            return TestClient(app)

    def test_read_schema_accepts_database_event(self):
        item_id = uuid.uuid4()
        payload = EventCatalogRead.model_validate(
            {
                "id": item_id,
                "event_name": "purchase",
                "event_category": "COMMERCE",
                "domain_scope": "retail",
            }
        )
        self.assertEqual(payload.id, item_id)

    def test_event_catalog_crud_endpoints_are_available(self):
        client = self.build_client()
        item_id = str(uuid.uuid4())

        listed = client.get("/event-catalog/?limit=500")
        created = client.post(
            "/event-catalog/",
            json={
                "event_name": "booking-confirmed",
                "event_category": "TRAVEL",
                "domain_scope": "travel",
                "is_conversion_default": True,
            },
        )
        updated = client.patch(
            f"/event-catalog/{item_id}",
            json={"status": "INACTIVE"},
        )
        deleted = client.delete(f"/event-catalog/{item_id}")

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["event_name"], "purchase")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["event_name"], "booking-confirmed")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["status"], "INACTIVE")
        self.assertEqual(deleted.status_code, 204)

    def test_event_catalog_rejects_invalid_public_values(self):
        client = self.build_client()

        invalid_name = client.post(
            "/event-catalog/",
            json={"event_name": "Purchase Completed", "event_category": "COMMERCE"},
        )
        invalid_category = client.post(
            "/event-catalog/",
            json={"event_name": "purchase-completed", "event_category": "UNKNOWN"},
        )

        self.assertEqual(invalid_name.status_code, 422)
        self.assertEqual(invalid_category.status_code, 422)


if __name__ == "__main__":
    unittest.main()
