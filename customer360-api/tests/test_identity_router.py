"""Unit tests for the Customer Identity Resolution profile-links endpoints
(core.routers.identity: profile_links_router + master_profiles_router's
GET /master-profiles/{id}/links) -- entirely against in-memory fakes, no
real PostgreSQL instance required.

`profile_links_router` and `get_master_profile_links` are hand-written (not
built via core.routers._generic.build_crud_router), so they can't be tested
by patching `core.routers._generic.CRUDBase` the way the generic-router
tests do. Instead this file monkeypatches the module-level `_link_crud`
instance on `core.routers.identity` directly -- safe because route handlers
look up module globals by name at call time, not at def time, so swapping
the attribute after import is picked up by every request.
"""

import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.routers.identity as identity_router
from core.database import get_db

DEMO_TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class FakeLinkCRUD:
    """In-memory stand-in for CRUDBase(CdpProfileLink)."""

    store: dict[uuid.UUID, SimpleNamespace] = {}
    last_list_kwargs: dict[str, Any] = {}
    last_list_skip_limit: tuple[int, int] = (0, 0)

    def __init__(self, model):
        self.model = model

    @classmethod
    def reset(cls):
        cls.store = {}
        cls.last_list_kwargs = {}
        cls.last_list_skip_limit = (0, 0)

    def list(self, db, *, skip=0, limit=100, **filters):
        FakeLinkCRUD.last_list_kwargs = filters
        FakeLinkCRUD.last_list_skip_limit = (skip, limit)
        results = list(FakeLinkCRUD.store.values())
        for field, value in filters.items():
            if value is not None:
                results = [r for r in results if getattr(r, field, None) == value]
        return results[skip : skip + limit]

    def get(self, db, pk: uuid.UUID) -> Optional[SimpleNamespace]:
        return FakeLinkCRUD.store.get(pk)

    def create(self, db, obj_in: dict[str, Any]) -> SimpleNamespace:
        obj = SimpleNamespace(
            link_id=uuid.uuid4(),
            created_at=datetime.now(timezone.utc),
            **obj_in,
        )
        FakeLinkCRUD.store[obj.link_id] = obj
        return obj

    def delete(self, db, db_obj: SimpleNamespace) -> None:
        FakeLinkCRUD.store.pop(db_obj.link_id, None)


class _FakeScalarsResult:
    """Stands in for a SQLAlchemy CursorResult supporting `.scalars().all()`."""

    def __init__(self, rows: list[Any]):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeFirstResult:
    """Stands in for a SQLAlchemy CursorResult supporting `.first()`."""

    def __init__(self, row: Any):
        self._row = row

    def first(self):
        return self._row


class _FakeSelectSession:
    """Minimal Session double recording every execute() call, returning a
    single canned `.scalars().all()` result."""

    def __init__(self, rows: list[Any]):
        self.result = _FakeScalarsResult(rows)
        self.executed: list[Any] = []

    def execute(self, stmt: Any, params: Optional[dict[str, Any]] = None) -> Any:
        self.executed.append(stmt)
        return self.result


def _link_payload(**overrides) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenant_id": str(DEMO_TENANT_ID),
        "raw_profile_id": str(uuid.uuid4()),
        "master_profile_id": str(uuid.uuid4()),
        "match_score": "1.0000",
        "match_method": "DynamicMatch",
    }
    payload.update(overrides)
    return payload


def _fake_link(**overrides) -> SimpleNamespace:
    defaults = {
        "link_id": uuid.uuid4(),
        "tenant_id": DEMO_TENANT_ID,
        "user_id": None,
        "raw_profile_id": uuid.uuid4(),
        "master_profile_id": uuid.uuid4(),
        "match_score": None,
        "match_method": "DynamicMatch",
        "status": "ACTIVE",
        "unlinked_at": None,
        "unlinked_reason": None,
        "unlinked_by": None,
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_master_profile(**overrides) -> SimpleNamespace:
    defaults = {
        "master_profile_id": uuid.uuid4(),
        "tenant_id": DEMO_TENANT_ID,
        "domain": "retail",
        "status_code": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_raw_profile(**overrides) -> SimpleNamespace:
    defaults = {
        "raw_profile_id": uuid.uuid4(),
        "tenant_id": DEMO_TENANT_ID,
        "user_id": None,
        "domain": "retail",
        "source_system": "moengage",
        "channel": "mobile_app",
        "external_customer_id": None,
        "full_name": None,
        "first_name": None,
        "last_name": None,
        "email": None,
        "phone_number": None,
        "national_id": None,
        "date_of_birth": None,
        "address_line1": None,
        "address_line2": None,
        "city": None,
        "state_province": None,
        "postal_code": None,
        "country": None,
        "company_name": None,
        "device_id": None,
        "advertising_id": None,
        "platform": None,
        "app_version": None,
        "push_token": None,
        "cookie_id": None,
        "ga_client_id": None,
        "session_id": None,
        "ip_address": None,
        "user_agent": None,
        "media_source": None,
        "campaign": None,
        "utm_source": None,
        "utm_medium": None,
        "utm_campaign": None,
        "event_name": None,
        "event_time": None,
        "event_payload": None,
        "status_code": 1,
        "processed_at": None,
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class ProfileLinksRouterTests(unittest.TestCase):
    """Covers /profile-links/ CRUD (list filters, get 200/404, create,
    delete 204/404, cache invalidation, and the new status/unlinked_*
    lifecycle fields)."""

    def setUp(self):
        FakeLinkCRUD.reset()
        self._original_link_crud = identity_router._link_crud
        identity_router._link_crud = FakeLinkCRUD(identity_router.CdpProfileLink)

        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)
        self.addCleanup(self._restore_link_crud)

        app = FastAPI()
        app.include_router(identity_router.profile_links_router)
        app.dependency_overrides[get_db] = lambda: None
        self.client = TestClient(app)

    def _restore_link_crud(self):
        identity_router._link_crud = self._original_link_crud

    # ------------------------------------------------------------------
    # CREATE
    # ------------------------------------------------------------------
    def test_create_profile_link_defaults_status_active(self):
        response = self.client.post("/profile-links/", json=_link_payload())
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["status"], "ACTIVE")
        self.assertIsNone(body["unlinked_at"])
        self.assertIn("link_id", body)

    def test_create_profile_link_accepts_explicit_lifecycle_fields(self):
        raw_id = str(uuid.uuid4())
        master_id = str(uuid.uuid4())
        payload = _link_payload(
            raw_profile_id=raw_id,
            master_profile_id=master_id,
            status="UNLINKED",
            unlinked_reason="Manual split by admin",
        )
        response = self.client.post("/profile-links/", json=payload)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["status"], "UNLINKED")
        self.assertEqual(body["unlinked_reason"], "Manual split by admin")

    def test_create_profile_link_rejects_invalid_status(self):
        response = self.client.post("/profile-links/", json=_link_payload(status="BOGUS"))
        self.assertEqual(response.status_code, 422)

    def test_create_profile_link_invalidates_cache(self):
        with patch("core.routers.identity.invalidate_prefix") as mock_invalidate:
            response = self.client.post("/profile-links/", json=_link_payload())
        self.assertEqual(response.status_code, 201)
        mock_invalidate.assert_called_once_with("profile_links")

    # ------------------------------------------------------------------
    # LIST
    # ------------------------------------------------------------------
    def test_list_profile_links_no_filters(self):
        link = _fake_link()
        FakeLinkCRUD.store[link.link_id] = link

        response = self.client.get("/profile-links/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(
            FakeLinkCRUD.last_list_kwargs,
            {"tenant_id": None, "raw_profile_id": None, "master_profile_id": None},
        )

    def test_list_profile_links_filters_by_tenant_id(self):
        tenant_id = uuid.uuid4()
        matching = _fake_link(tenant_id=tenant_id)
        other = _fake_link(tenant_id=uuid.uuid4())
        FakeLinkCRUD.store[matching.link_id] = matching
        FakeLinkCRUD.store[other.link_id] = other

        response = self.client.get(f"/profile-links/?tenant_id={tenant_id}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["link_id"], str(matching.link_id))

    def test_list_profile_links_filters_by_raw_profile_id(self):
        raw_id = uuid.uuid4()
        matching = _fake_link(raw_profile_id=raw_id)
        other = _fake_link()
        FakeLinkCRUD.store[matching.link_id] = matching
        FakeLinkCRUD.store[other.link_id] = other

        response = self.client.get(f"/profile-links/?raw_profile_id={raw_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

    def test_list_profile_links_filters_by_master_profile_id(self):
        master_id = uuid.uuid4()
        matching = _fake_link(master_profile_id=master_id)
        other = _fake_link()
        FakeLinkCRUD.store[matching.link_id] = matching
        FakeLinkCRUD.store[other.link_id] = other

        response = self.client.get(f"/profile-links/?master_profile_id={master_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

    def test_list_profile_links_passes_skip_and_limit(self):
        response = self.client.get("/profile-links/?skip=5&limit=10")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FakeLinkCRUD.last_list_skip_limit, (5, 10))

    # ------------------------------------------------------------------
    # GET single
    # ------------------------------------------------------------------
    def test_get_profile_link_found(self):
        link = _fake_link()
        FakeLinkCRUD.store[link.link_id] = link

        response = self.client.get(f"/profile-links/{link.link_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["link_id"], str(link.link_id))

    def test_get_profile_link_not_found(self):
        response = self.client.get(f"/profile-links/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # DELETE
    # ------------------------------------------------------------------
    def test_delete_profile_link_found(self):
        link = _fake_link()
        FakeLinkCRUD.store[link.link_id] = link

        response = self.client.delete(f"/profile-links/{link.link_id}")

        self.assertEqual(response.status_code, 204)
        self.assertNotIn(link.link_id, FakeLinkCRUD.store)

    def test_delete_profile_link_not_found(self):
        response = self.client.delete(f"/profile-links/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)

    def test_delete_profile_link_invalidates_cache(self):
        link = _fake_link()
        FakeLinkCRUD.store[link.link_id] = link

        with patch("core.routers.identity.invalidate_prefix") as mock_invalidate:
            response = self.client.delete(f"/profile-links/{link.link_id}")

        self.assertEqual(response.status_code, 204)
        mock_invalidate.assert_called_once_with("profile_links")


class MasterProfileLinksEndpointTests(unittest.TestCase):
    """Covers GET /master-profiles/{id}/links (direct db.execute(select(...))
    path, not routed through CRUDBase)."""

    def setUp(self):
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        app = FastAPI()
        app.include_router(identity_router.master_profiles_router)
        self.app = app
        self.master_profile_id = uuid.uuid4()

    def _client_with_rows(self, rows: list[Any]) -> TestClient:
        session = _FakeSelectSession(rows)
        self.app.dependency_overrides[get_db] = lambda: session
        return TestClient(self.app)

    def test_returns_links_for_master_profile(self):
        rows = [_fake_link(master_profile_id=self.master_profile_id) for _ in range(3)]
        client = self._client_with_rows(rows)

        response = client.get(f"/master-profiles/{self.master_profile_id}/links")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 3)

    def test_returns_empty_list_when_no_links(self):
        client = self._client_with_rows([])

        response = client.get(f"/master-profiles/{self.master_profile_id}/links")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_limit_query_param_is_forwarded_to_the_statement(self):
        client = self._client_with_rows([])

        response = client.get(f"/master-profiles/{self.master_profile_id}/links?limit=5")

        self.assertEqual(response.status_code, 200)
        # Bounded by idx_cdp_profile_links_master -- just assert the compiled
        # statement actually carries a LIMIT clause, not an unbounded scan.
        session = self.app.dependency_overrides[get_db]()
        compiled = str(session.executed[-1])
        self.assertIn("LIMIT", compiled.upper())


class MasterProfileLinkedRawDetailEndpointTests(unittest.TestCase):
    """Covers GET /master-profiles/{id}/linked-raw-profiles/{raw_profile_id}."""

    def setUp(self):
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        self._original_master_crud = identity_router._master_crud
        self.master_profile = _fake_master_profile()
        identity_router._master_crud = SimpleNamespace(
            get=lambda db, pk: self.master_profile if pk == self.master_profile.master_profile_id else None
        )
        self.addCleanup(self._restore_master_crud)

        app = FastAPI()
        app.include_router(identity_router.master_profiles_router)
        self.app = app

    def _restore_master_crud(self):
        identity_router._master_crud = self._original_master_crud

    def test_returns_linked_raw_profile_detail(self):
        link = _fake_link(
            tenant_id=self.master_profile.tenant_id,
            master_profile_id=self.master_profile.master_profile_id,
        )
        raw_profile = _fake_raw_profile(
            raw_profile_id=link.raw_profile_id,
            tenant_id=self.master_profile.tenant_id,
            source_system="web_tracking",
            event_name="checkout_started",
            event_payload={"cart_value": 129.9},
        )

        session = SimpleNamespace(execute=lambda stmt: _FakeFirstResult((link, raw_profile)))
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.get(
            f"/master-profiles/{self.master_profile.master_profile_id}/linked-raw-profiles/{link.raw_profile_id}"
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["link"]["link_id"], str(link.link_id))
        self.assertEqual(body["raw_profile"]["raw_profile_id"], str(raw_profile.raw_profile_id))
        self.assertEqual(body["raw_profile"]["source_system"], "web_tracking")

    def test_returns_404_when_master_profile_not_found(self):
        session = SimpleNamespace(execute=lambda stmt: _FakeFirstResult(None))
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.get(
            f"/master-profiles/{uuid.uuid4()}/linked-raw-profiles/{uuid.uuid4()}"
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.json()["detail"].lower())

    def test_returns_404_when_raw_profile_is_not_linked(self):
        session = SimpleNamespace(execute=lambda stmt: _FakeFirstResult(None))
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.get(
            f"/master-profiles/{self.master_profile.master_profile_id}/linked-raw-profiles/{uuid.uuid4()}"
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("not linked", response.json()["detail"].lower())


class MasterProfilesPaginationEndpointTests(unittest.TestCase):
    """Covers GET /master-profiles/ paginated response envelope."""

    def setUp(self):
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        self._domain_patcher = patch(
            "core.utils.domains.get_active_domain_codes",
            return_value={"retail", "banking", "healthcare", "real_estate", "travel", "media", "education"},
        )
        self._domain_patcher.start()
        self.addCleanup(self._domain_patcher.stop)

        app = FastAPI()
        app.include_router(identity_router.master_profiles_router)
        self.session = object()
        app.dependency_overrides[get_db] = lambda: self.session
        self.client = TestClient(app)

    def test_returns_paginated_envelope(self):
        master_profile_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        fake_payload = {
            "items": [
                {
                    "master_profile_id": master_profile_id,
                    "tenant_id": tenant_id,
                    "domain": "retail",
                    "linked_raw_profile_count": 4,
                    "status_code": 1,
                }
            ],
            "pagination": {
                "page": 2,
                "page_size": 25,
                "total": 120,
                "total_pages": 5,
                "has_prev": True,
                "has_next": True,
            },
        }

        with patch("core.routers.identity.identity_crud.list_master_profiles_page", return_value=fake_payload):
            response = self.client.get("/master-profiles/?page=2&page_size=25")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("items", body)
        self.assertIn("pagination", body)
        self.assertEqual(body["pagination"]["page"], 2)
        self.assertEqual(body["pagination"]["page_size"], 25)
        self.assertEqual(body["pagination"]["total"], 120)
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["linked_raw_profile_count"], 4)

    def test_forwards_filters_and_pagination_params_to_crud(self):
        with patch("core.routers.identity.identity_crud.list_master_profiles_page", return_value={"items": [], "pagination": {
            "page": 1,
            "page_size": 100,
            "total": 0,
            "total_pages": 1,
            "has_prev": False,
            "has_next": False,
        }}) as mock_list:
            response = self.client.get(
                "/master-profiles/?tenant_id=11111111-1111-1111-1111-111111111111"
                "&domain=healthcare&lifecycle_stage=customer&membership_tier=Gold"
                "&churn_risk_tier=high&linked_raw_profile_count_min=2"
                "&q=nguyen&page=3&page_size=15&days=30"
            )

        self.assertEqual(response.status_code, 200)
        mock_list.assert_called_once_with(
            self.session,
            tenant_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            domain="healthcare",
            lifecycle_stage="customer",
            domain_attribute_key=None,
            domain_attribute_value=None,
            membership_tier="Gold",
            clv_segment=None,
            churn_risk_tier="high",
            linked_raw_profile_count_min=2,
            q="nguyen",
            days=30,
            page=3,
            page_size=15,
        )

    def test_rejects_invalid_domain(self):
        response = self.client.get("/master-profiles/?domain=finance")

        self.assertEqual(response.status_code, 422)


class _ScalarsAllResult:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _ScalarOneOrNoneResult:
    def __init__(self, value: Any):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _RowsResult:
    """Result double for plain (non-scalar) column-tuple selects, e.g.
    ``select(Model.id, Model.code)`` -- returns raw tuples from ``.all()``."""

    def __init__(self, rows: list[Any]):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeMultiExecSession:
    """Session double for endpoints that issue several db.execute() calls in
    sequence (e.g. domain validation, then a lookup, then a write) -- returns
    one canned result per call, in order."""

    def __init__(self, results: list[Any]):
        self._results = list(results)
        self.executed: list[Any] = []
        self.added: list[Any] = []
        self.committed = False
        self.refreshed: list[Any] = []

    def execute(self, stmt: Any) -> Any:
        self.executed.append(stmt)
        return self._results.pop(0)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.committed = True

    def refresh(self, obj: Any) -> None:
        # Simulates the server_default values Postgres would have assigned
        # on the real INSERT this fake never actually performs.
        self.refreshed.append(obj)
        if getattr(obj, "domain_profile_id", None) is None:
            obj.domain_profile_id = uuid.uuid4()
        if getattr(obj, "status_code", None) is None:
            obj.status_code = 1
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)
        if getattr(obj, "updated_at", None) is None:
            obj.updated_at = datetime.now(timezone.utc)


class MasterProfileDomainProfilesEndpointTests(unittest.TestCase):
    """Covers GET /master-profiles/{id}/domain-profiles."""

    def setUp(self):
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        self._original_master_crud = identity_router._master_crud
        self.master_profile = _fake_master_profile()
        identity_router._master_crud = SimpleNamespace(
            get=lambda db, pk: self.master_profile if pk == self.master_profile.master_profile_id else None
        )
        self.addCleanup(lambda: setattr(identity_router, "_master_crud", self._original_master_crud))

        self.app = FastAPI()
        self.app.include_router(identity_router.master_profiles_router)

    def test_returns_404_for_missing_master_profile(self):
        session = _FakeMultiExecSession([])
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.get(f"/master-profiles/{uuid.uuid4()}/domain-profiles")

        self.assertEqual(response.status_code, 404)

    def test_returns_domain_profiles_for_master_profile(self):
        domain_id = uuid.uuid4()
        domain_profile = SimpleNamespace(
            domain_profile_id=uuid.uuid4(),
            tenant_id=self.master_profile.tenant_id,
            master_profile_id=self.master_profile.master_profile_id,
            domain_id=domain_id,
            profile_name=None,
            lifecycle_stage=None,
            persona_name=None,
            persona_summary=None,
            engagement_score=None,
            domain_attributes={"loyalty_id": "LOY-1"},
            analytics={},
            first_activity_at=None,
            last_activity_at=None,
            status_code=1,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session = _FakeMultiExecSession(
            [_ScalarsAllResult([domain_profile]), _RowsResult([(domain_id, "retail")])]
        )
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.get(f"/master-profiles/{self.master_profile.master_profile_id}/domain-profiles")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["domain_attributes"], {"loyalty_id": "LOY-1"})
        self.assertEqual(body[0]["domain_code"], "retail")


class MasterProfileDomainAttributeUpsertEndpointTests(unittest.TestCase):
    """Covers POST /master-profiles/{id}/domain-attributes."""

    def setUp(self):
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        self._original_master_crud = identity_router._master_crud
        self.master_profile = _fake_master_profile()
        identity_router._master_crud = SimpleNamespace(
            get=lambda db, pk: self.master_profile if pk == self.master_profile.master_profile_id else None
        )
        self.addCleanup(lambda: setattr(identity_router, "_master_crud", self._original_master_crud))

        self.app = FastAPI()
        self.app.include_router(identity_router.master_profiles_router)

    def test_returns_404_for_missing_master_profile(self):
        session = _FakeMultiExecSession([])
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.post(
            f"/master-profiles/{uuid.uuid4()}/domain-attributes",
            json={"domain": "banking", "attribute_key": "risk_segment", "attribute_value": "high"},
        )

        self.assertEqual(response.status_code, 404)

    def test_returns_422_for_unknown_domain(self):
        session = _FakeMultiExecSession([_ScalarsAllResult(["banking", "retail"])])
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.post(
            f"/master-profiles/{self.master_profile.master_profile_id}/domain-attributes",
            json={"domain": "bogus_domain", "attribute_key": "risk_segment", "attribute_value": "high"},
        )

        self.assertEqual(response.status_code, 422)

    def test_creates_new_domain_profile_when_none_exists(self):
        domain_id = uuid.uuid4()
        session = _FakeMultiExecSession(
            [
                _ScalarsAllResult(["banking", "retail"]),  # validate_domain_value
                _ScalarOneOrNoneResult(domain_id),  # domain_id lookup
                _ScalarOneOrNoneResult(None),  # no existing domain profile
            ]
        )
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.post(
            f"/master-profiles/{self.master_profile.master_profile_id}/domain-attributes",
            json={"domain": "banking", "attribute_key": "risk_segment", "attribute_value": "high"},
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["domain_attributes"], {"risk_segment": "high"})
        self.assertTrue(session.committed)
        self.assertEqual(len(session.added), 1)

    def test_merges_into_existing_domain_profile_without_dropping_other_keys(self):
        domain_id = uuid.uuid4()
        existing = SimpleNamespace(
            domain_profile_id=uuid.uuid4(),
            tenant_id=self.master_profile.tenant_id,
            master_profile_id=self.master_profile.master_profile_id,
            domain_id=domain_id,
            profile_name=None,
            lifecycle_stage=None,
            persona_name=None,
            persona_summary=None,
            engagement_score=None,
            domain_attributes={"loyalty_id": "LOY-1"},
            analytics={},
            first_activity_at=None,
            last_activity_at=None,
            status_code=1,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session = _FakeMultiExecSession(
            [
                _ScalarsAllResult(["banking", "retail"]),
                _ScalarOneOrNoneResult(domain_id),
                _ScalarOneOrNoneResult(existing),
            ]
        )
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.post(
            f"/master-profiles/{self.master_profile.master_profile_id}/domain-attributes",
            json={"domain": "banking", "attribute_key": "risk_segment", "attribute_value": "high"},
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["domain_attributes"], {"loyalty_id": "LOY-1", "risk_segment": "high"})
        self.assertTrue(session.committed)
        self.assertEqual(len(session.added), 0)


if __name__ == "__main__":
    unittest.main()
