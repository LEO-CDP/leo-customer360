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


class MasterProfilesPaginationEndpointTests(unittest.TestCase):
    """Covers GET /master-profiles/ paginated response envelope."""

    def setUp(self):
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

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
                "&domain=retail&lifecycle_stage=customer&q=nguyen&page=3&page_size=15&days=30"
            )

        self.assertEqual(response.status_code, 200)
        mock_list.assert_called_once_with(
            self.session,
            tenant_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            domain="retail",
            lifecycle_stage="customer",
            q="nguyen",
            days=30,
            page=3,
            page_size=15,
        )


if __name__ == "__main__":
    unittest.main()
