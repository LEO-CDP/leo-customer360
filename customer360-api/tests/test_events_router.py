"""Unit tests for /events router create behavior.

Covers POST /events auto-link flow:
- reject payloads that have neither raw_profile_id nor identity hints,
- validate provided raw_profile_id belongs to same tenant/domain,
- auto-create cdp_raw_profiles_stage row when only hashed identity is provided.
"""

import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import get_db
from core.models.events import CdpRawEvent
from core.models.identity import CdpRawProfileStage
from core.routers.events_api import router as events_router


class _FakeScalarResult:
    def __init__(self, value: Any):
        self._value = value

    def scalars(self):
        return self

    def first(self):
        return self._value


class _FakeEventsSession:
    def __init__(self, scripted_execute_results: Optional[list[Any]] = None):
        self.scripted_execute_results = list(scripted_execute_results or [])
        self.added: list[Any] = []
        self.executed: list[tuple[str, Optional[dict[str, Any]]]] = []
        self.committed = False
        self.rolled_back = False

    def execute(self, stmt: Any, params: Optional[dict[str, Any]] = None):
        self.executed.append((str(stmt), params))
        if self.scripted_execute_results:
            return _FakeScalarResult(self.scripted_execute_results.pop(0))
        return _FakeScalarResult(None)

    def add(self, obj: Any):
        if isinstance(obj, CdpRawProfileStage) and getattr(obj, "raw_profile_id", None) is None:
            obj.raw_profile_id = uuid.uuid4()
        if isinstance(obj, CdpRawEvent) and getattr(obj, "event_id", None) is None:
            obj.event_id = uuid.uuid4()
        self.added.append(obj)

    def flush(self):
        return None

    def commit(self):
        self.committed = True

    def refresh(self, obj: Any):
        return None

    def rollback(self):
        self.rolled_back = True


class EventsCreateTests(unittest.TestCase):
    def setUp(self):
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        self.app = FastAPI()
        self.app.include_router(events_router)

    def _client_for(self, fake_session: _FakeEventsSession) -> TestClient:
        self.app.dependency_overrides[get_db] = lambda: fake_session
        return TestClient(self.app)

    def test_create_event_rejects_when_no_raw_profile_and_no_identity(self):
        fake_session = _FakeEventsSession()
        client = self._client_for(fake_session)

        response = client.post(
            "/events/",
            json={
                "tenant_id": str(uuid.uuid4()),
                "domain": "retail",
                "source_system": "WebTracking",
                "event_name": "page_view",
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_create_event_rejects_unknown_raw_profile_id(self):
        fake_session = _FakeEventsSession(scripted_execute_results=[None])
        client = self._client_for(fake_session)

        response = client.post(
            "/events/",
            json={
                "tenant_id": str(uuid.uuid4()),
                "domain": "retail",
                "raw_profile_id": str(uuid.uuid4()),
                "source_system": "WebTracking",
                "event_name": "page_view",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("raw_profile_id was provided", response.json()["detail"])

    def test_create_event_auto_creates_raw_profile_for_hashed_identity(self):
        fake_session = _FakeEventsSession(scripted_execute_results=[None])
        client = self._client_for(fake_session)

        hashed_email = "a" * 64
        event_time = datetime.now(timezone.utc).isoformat()
        tenant_id = str(uuid.uuid4())

        response = client.post(
            "/events/",
            json={
                "tenant_id": tenant_id,
                "domain": "retail",
                "source_system": "MoEngage",
                "event_name": "purchase",
                "event_category": "COMMERCE",
                "email": hashed_email,
                "event_time": event_time,
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIsNotNone(body["raw_profile_id"])
        self.assertIsNone(body["master_profile_id"])
        self.assertEqual(body["source_system"], "MoEngage")
        self.assertEqual(body["event_name"], "purchase")

        self.assertEqual(len(fake_session.added), 2)
        self.assertIsInstance(fake_session.added[0], CdpRawProfileStage)
        self.assertIsInstance(fake_session.added[1], CdpRawEvent)
        self.assertEqual(str(fake_session.added[0].tenant_id), tenant_id)
        self.assertEqual(fake_session.added[0].email, hashed_email)
        self.assertEqual(fake_session.added[1].raw_profile_id, fake_session.added[0].raw_profile_id)
        self.assertTrue(fake_session.committed)

    def test_create_event_reuses_existing_raw_profile_by_identity(self):
        existing_raw_id = uuid.uuid4()
        existing = SimpleNamespace(raw_profile_id=existing_raw_id)
        fake_session = _FakeEventsSession(scripted_execute_results=[existing])
        client = self._client_for(fake_session)

        response = client.post(
            "/events/",
            json={
                "tenant_id": str(uuid.uuid4()),
                "domain": "banking",
                "source_system": "CoreBanking",
                "event_name": "login",
                "phone_number": "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["raw_profile_id"], str(existing_raw_id))
        self.assertEqual(len(fake_session.added), 1)
        self.assertIsInstance(fake_session.added[0], CdpRawEvent)

    def test_create_event_is_idempotent_when_dedup_key_replayed(self):
        tenant_id = uuid.uuid4()
        existing_event = SimpleNamespace(
            event_id=uuid.uuid4(),
            event_time=datetime.now(timezone.utc),
            tenant_id=tenant_id,
            user_id=None,
            domain="retail",
            master_profile_id=None,
            raw_profile_id=uuid.uuid4(),
            external_customer_id=None,
            device_id=None,
            advertising_id=None,
            cookie_id=None,
            session_id=None,
            source_system="MoEngage",
            event_dedup_key="msg-001",
            channel="mobile_app",
            platform=None,
            ip_address=None,
            user_agent=None,
            event_category="COMMERCE",
            event_name="purchase",
            is_conversion=False,
            entity_type=None,
            entity_id=None,
            event_value=None,
            currency="USD",
            transaction_id=None,
            transaction_status=None,
            location_code=None,
            location_name=None,
            event_payload=None,
            created_at=datetime.now(timezone.utc),
        )
        fake_session = _FakeEventsSession(scripted_execute_results=[existing_event])
        client = self._client_for(fake_session)

        response = client.post(
            "/events/",
            json={
                "tenant_id": str(tenant_id),
                "domain": "retail",
                "source_system": "MoEngage",
                "event_dedup_key": "msg-001",
                "event_name": "purchase",
                "event_category": "COMMERCE",
                "email": "a" * 64,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["event_id"], str(existing_event.event_id))
        self.assertEqual(len(fake_session.added), 0)
        self.assertFalse(fake_session.committed)

    def test_bulk_create_events_accepts_mixed_existing_and_new_rows(self):
        tenant_id = str(uuid.uuid4())
        existing_raw_id = uuid.uuid4()
        existing_raw = SimpleNamespace(raw_profile_id=existing_raw_id)
        fake_session = _FakeEventsSession(scripted_execute_results=[existing_raw, None])
        client = self._client_for(fake_session)

        response = client.post(
            "/events/bulk",
            json=[
                {
                    "tenant_id": tenant_id,
                    "domain": "retail",
                    "source_system": "WebTracking",
                    "raw_profile_id": str(existing_raw_id),
                    "event_name": "page_view",
                },
                {
                    "tenant_id": tenant_id,
                    "domain": "retail",
                    "source_system": "MoEngage",
                    "event_name": "purchase",
                    "event_category": "COMMERCE",
                    "email": "a" * 64,
                },
            ],
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(len(body), 2)
        self.assertIsNotNone(body[0]["raw_profile_id"])
        self.assertIsNotNone(body[1]["raw_profile_id"])
        self.assertEqual(len(fake_session.added), 3)
        self.assertTrue(fake_session.committed)

    def test_bulk_create_is_idempotent_for_replayed_dedup_key(self):
        tenant_id = uuid.uuid4()
        existing_event = SimpleNamespace(
            event_id=uuid.uuid4(),
            event_time=datetime.now(timezone.utc),
            tenant_id=tenant_id,
            user_id=None,
            domain="retail",
            master_profile_id=None,
            raw_profile_id=uuid.uuid4(),
            external_customer_id=None,
            device_id=None,
            advertising_id=None,
            cookie_id=None,
            session_id=None,
            source_system="MoEngage",
            event_dedup_key="bulk-msg-001",
            channel="mobile_app",
            platform=None,
            ip_address=None,
            user_agent=None,
            event_category="COMMERCE",
            event_name="purchase",
            is_conversion=False,
            entity_type=None,
            entity_id=None,
            event_value=None,
            currency="USD",
            transaction_id=None,
            transaction_status=None,
            location_code=None,
            location_name=None,
            event_payload=None,
            created_at=datetime.now(timezone.utc),
        )
        fake_session = _FakeEventsSession(scripted_execute_results=[existing_event])
        client = self._client_for(fake_session)

        response = client.post(
            "/events/bulk",
            json=[
                {
                    "tenant_id": str(tenant_id),
                    "domain": "retail",
                    "source_system": "MoEngage",
                    "event_dedup_key": "bulk-msg-001",
                    "event_name": "purchase",
                    "event_category": "COMMERCE",
                    "email": "a" * 64,
                }
            ],
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["event_id"], str(existing_event.event_id))
        self.assertEqual(len(fake_session.added), 0)
        self.assertFalse(fake_session.committed)

    def test_bulk_create_rejects_empty_payload(self):
        fake_session = _FakeEventsSession()
        client = self._client_for(fake_session)

        response = client.post("/events/bulk", json=[])

        self.assertEqual(response.status_code, 400)
        self.assertIn("at least one event", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
