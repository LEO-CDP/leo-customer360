"""Unit tests for the Customer Persona Resolution ("identity understanding")
endpoints in core.routers.identity: customer_personas_router,
persona_features_router, persona_score_details_router, persona_history_router,
plus master_profiles_router's GET /{id}/persona and GET /{id}/persona-history.

All hand-written routers here are tested the same way as
test_identity_router.py's profile_links_router coverage: monkeypatch the
module-level CRUD singleton(s) on core.routers.identity with in-memory
fakes, no real PostgreSQL instance required.
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


# ---------------------------------------------------------------------------
# In-memory CRUD fakes (mirrors test_identity_router.py's FakeLinkCRUD)
# ---------------------------------------------------------------------------


class FakePersonaCRUD:
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
        FakePersonaCRUD.last_list_kwargs = filters
        FakePersonaCRUD.last_list_skip_limit = (skip, limit)
        results = list(FakePersonaCRUD.store.values())
        for field, value in filters.items():
            if value is not None:
                results = [r for r in results if getattr(r, field, None) == value]
        return results[skip : skip + limit]

    def get(self, db, pk: uuid.UUID) -> Optional[SimpleNamespace]:
        return FakePersonaCRUD.store.get(pk)

    def create(self, db, obj_in: dict[str, Any]) -> SimpleNamespace:
        obj = SimpleNamespace(persona_id=uuid.uuid4(), computed_at=datetime.now(timezone.utc), **obj_in)
        FakePersonaCRUD.store[obj.persona_id] = obj
        return obj

    def update(self, db, db_obj: SimpleNamespace, obj_in: dict[str, Any]) -> SimpleNamespace:
        for field, value in obj_in.items():
            setattr(db_obj, field, value)
        return db_obj

    def delete(self, db, db_obj: SimpleNamespace) -> None:
        FakePersonaCRUD.store.pop(db_obj.persona_id, None)


def _make_child_crud(pk_field: str, pk_factory):
    """Builds a small in-memory CRUD fake class for the append-only
    persona-features/score-details/history child tables (list/get/create
    only -- no update/delete on these, matching the real routers)."""

    class _FakeChildCRUD:
        store: dict[Any, SimpleNamespace] = {}
        last_list_kwargs: dict[str, Any] = {}

        def __init__(self, model):
            self.model = model

        @classmethod
        def reset(cls):
            cls.store = {}
            cls.last_list_kwargs = {}

        def list(self, db, *, skip=0, limit=100, **filters):
            _FakeChildCRUD.last_list_kwargs = filters
            results = list(_FakeChildCRUD.store.values())
            for field, value in filters.items():
                if value is not None:
                    results = [r for r in results if getattr(r, field, None) == value]
            return results[skip : skip + limit]

        def get(self, db, pk) -> Optional[SimpleNamespace]:
            return _FakeChildCRUD.store.get(pk)

        def create(self, db, obj_in: dict[str, Any]) -> SimpleNamespace:
            pk_value = obj_in.get(pk_field) or pk_factory()
            obj = SimpleNamespace(**{pk_field: pk_value}, **{k: v for k, v in obj_in.items() if k != pk_field})
            _FakeChildCRUD.store[pk_value] = obj
            return obj

    return _FakeChildCRUD


FakeFeatureCRUD = _make_child_crud("feature_id", lambda: uuid.uuid4())
FakeScoreDetailCRUD = _make_child_crud("score_id", lambda: uuid.uuid4())
FakeHistoryCRUD = _make_child_crud("history_id", lambda: uuid.uuid4())


class _FakeScalarsResult:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeGetExecSession:
    """Minimal Session double supporting both db.get(Model, pk) (dict-backed
    per (model, pk) tuple) and db.execute(select(...)) (canned rows) -- what
    the hand-written master_profiles_router persona endpoints need."""

    def __init__(self, get_results: Optional[dict] = None, execute_rows: Optional[list] = None):
        self._get_results = get_results or {}
        self.execute_result = _FakeScalarsResult(execute_rows or [])
        self.executed: list[Any] = []

    def get(self, model, pk):
        return self._get_results.get((model, pk))

    def execute(self, stmt, params=None):
        self.executed.append(stmt)
        return self.execute_result


def _persona_payload(**overrides) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenant_id": str(DEMO_TENANT_ID),
        "domain": "retail",
        "master_profile_id": str(uuid.uuid4()),
        "persona_code": "retail_high_value_customer",
        "persona_name": "Savvy Retail Shopper #4f2a9c",
        "persona_category": "High Value Retail Shopper",
        "persona_summary": "A high-value retail customer with strong engagement.",
        "persona_score": "72.50",
        "lifecycle_stage": "customer",
        "customer_value_tier": "high_value",
        "risk_level": "low",
        "next_best_action": "Offer a loyalty upsell or premium tier upgrade.",
        "llm_provider": "offline-heuristic",
        "llm_model": "persona-engine-rule-based-v1",
    }
    payload.update(overrides)
    return payload


def _fake_persona(**overrides) -> SimpleNamespace:
    defaults = {
        "persona_id": uuid.uuid4(),
        "tenant_id": DEMO_TENANT_ID,
        "domain": "retail",
        "master_profile_id": uuid.uuid4(),
        "persona_code": "retail_high_value_customer",
        "persona_name": "Savvy Retail Shopper #4f2a9c",
        "persona_category": "High Value Retail Shopper",
        "persona_summary": "A high-value retail customer with strong engagement.",
        "persona_score": "72.50",
        "confidence_score": "0.8000",
        "behavior_score": "65.00",
        "engagement_score": "70.00",
        "financial_score": "80.00",
        "loyalty_score": "75.00",
        "relationship_score": "60.00",
        "risk_score": "20.00",
        "lifecycle_stage": "customer",
        "customer_value_tier": "high_value",
        "risk_level": "low",
        "next_best_action": "Offer a loyalty upsell or premium tier upgrade.",
        "llm_provider": "offline-heuristic",
        "llm_model": "persona-engine-rule-based-v1",
        "computed_version": 1,
        "is_active": True,
        "computed_at": datetime.now(timezone.utc),
        "expires_at": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class CustomerPersonasRouterTests(unittest.TestCase):
    """Covers /customer-personas/ CRUD (list filters, get 200/404, create,
    patch, delete 204/404, cache invalidation)."""

    def setUp(self):
        FakePersonaCRUD.reset()
        self._original_crud = identity_router._persona_crud
        identity_router._persona_crud = FakePersonaCRUD(identity_router.CdpCustomerPersona)

        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        self._domain_patcher = patch(
            "core.utils.domains.get_active_domain_codes",
            return_value={"retail", "banking", "healthcare", "real_estate", "travel", "media", "education"},
        )
        self._domain_patcher.start()
        self.addCleanup(self._domain_patcher.stop)
        self.addCleanup(self._restore_crud)

        app = FastAPI()
        app.include_router(identity_router.customer_personas_router)
        app.dependency_overrides[get_db] = lambda: None
        self.client = TestClient(app)

    def _restore_crud(self):
        identity_router._persona_crud = self._original_crud

    def test_create_persona(self):
        response = self.client.post("/customer-personas/", json=_persona_payload())
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["persona_code"], "retail_high_value_customer")
        self.assertTrue(body["is_active"])
        self.assertEqual(body["computed_version"], 1)

    def test_create_persona_rejects_invalid_risk_level(self):
        response = self.client.post("/customer-personas/", json=_persona_payload(risk_level="extreme"))
        self.assertEqual(response.status_code, 422)

    def test_create_persona_rejects_invalid_domain(self):
        response = self.client.post("/customer-personas/", json=_persona_payload(domain="finance"))
        self.assertEqual(response.status_code, 422)

    def test_create_persona_invalidates_cache(self):
        with patch("core.routers.identity.invalidate_prefix") as mock_invalidate:
            response = self.client.post("/customer-personas/", json=_persona_payload())
        self.assertEqual(response.status_code, 201)
        mock_invalidate.assert_called_once_with("customer_personas")

    def test_list_filters_by_master_profile_id(self):
        master_id = uuid.uuid4()
        matching = _fake_persona(master_profile_id=master_id)
        other = _fake_persona()
        FakePersonaCRUD.store[matching.persona_id] = matching
        FakePersonaCRUD.store[other.persona_id] = other

        response = self.client.get(f"/customer-personas/?master_profile_id={master_id}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["persona_id"], str(matching.persona_id))

    def test_list_filters_by_is_active(self):
        active = _fake_persona(is_active=True)
        inactive = _fake_persona(is_active=False)
        FakePersonaCRUD.store[active.persona_id] = active
        FakePersonaCRUD.store[inactive.persona_id] = inactive

        response = self.client.get("/customer-personas/?is_active=true")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["persona_id"], str(active.persona_id))

    def test_list_filters_by_domain(self):
        retail_persona = _fake_persona(domain="retail")
        banking_persona = _fake_persona(domain="banking")
        healthcare_persona = _fake_persona(domain="healthcare")
        FakePersonaCRUD.store[retail_persona.persona_id] = retail_persona
        FakePersonaCRUD.store[banking_persona.persona_id] = banking_persona
        FakePersonaCRUD.store[healthcare_persona.persona_id] = healthcare_persona

        response = self.client.get("/customer-personas/?domain=healthcare")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["persona_id"], str(healthcare_persona.persona_id))

    def test_list_rejects_invalid_domain(self):
        response = self.client.get("/customer-personas/?domain=finance")
        self.assertEqual(response.status_code, 422)

    def test_get_persona_not_found(self):
        response = self.client.get(f"/customer-personas/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)

    def test_patch_persona(self):
        persona = _fake_persona()
        FakePersonaCRUD.store[persona.persona_id] = persona

        response = self.client.patch(
            f"/customer-personas/{persona.persona_id}", json={"is_active": False, "risk_level": "high"}
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["is_active"])
        self.assertEqual(body["risk_level"], "high")

    def test_patch_persona_not_found(self):
        response = self.client.patch(f"/customer-personas/{uuid.uuid4()}", json={"is_active": False})
        self.assertEqual(response.status_code, 404)

    def test_delete_persona(self):
        persona = _fake_persona()
        FakePersonaCRUD.store[persona.persona_id] = persona

        response = self.client.delete(f"/customer-personas/{persona.persona_id}")

        self.assertEqual(response.status_code, 204)
        self.assertNotIn(persona.persona_id, FakePersonaCRUD.store)

    def test_delete_persona_not_found(self):
        response = self.client.delete(f"/customer-personas/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)

    def test_get_persona_analytics_summary(self):
        payload = {
            "total_personas": 10,
            "active_personas": 8,
            "inactive_personas": 2,
            "unique_master_profiles": 7,
            "avg_persona_score": 65.2,
            "avg_confidence_score": 0.8123,
            "by_domain": [{"value": "retail", "count": 6}],
            "by_category": [{"value": "High Value", "count": 4}],
            "by_risk_level": [{"value": "low", "count": 5}],
            "by_value_tier": [{"value": "gold", "count": 3}],
        }

        with patch("core.routers.identity.identity_crud.persona_analytics_summary", return_value=payload) as mock_summary:
            response = self.client.get("/customer-personas/analytics/summary?domain=healthcare&is_active=true&days=30")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total_personas"], 10)
        self.assertEqual(body["active_personas"], 8)
        self.assertEqual(body["avg_confidence_score"], 0.8123)
        mock_summary.assert_called_once()
        _, kwargs = mock_summary.call_args
        self.assertEqual(kwargs["domain"], "healthcare")
        self.assertTrue(kwargs["is_active"])
        self.assertEqual(kwargs["days"], 30)

    def test_get_persona_analytics_summary_rejects_invalid_domain(self):
        response = self.client.get("/customer-personas/analytics/summary?domain=finance")
        self.assertEqual(response.status_code, 422)


class PersonaFeaturesRouterTests(unittest.TestCase):
    def setUp(self):
        FakePersonaCRUD.reset()
        FakeFeatureCRUD.reset()
        self._original_persona_crud = identity_router._persona_crud
        self._original_feature_crud = identity_router._persona_feature_crud
        identity_router._persona_crud = FakePersonaCRUD(identity_router.CdpCustomerPersona)
        identity_router._persona_feature_crud = FakeFeatureCRUD(identity_router.CdpPersonaFeature)

        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)
        self.addCleanup(self._restore)

        app = FastAPI()
        app.include_router(identity_router.customer_personas_router)
        app.include_router(identity_router.persona_features_router)
        app.dependency_overrides[get_db] = lambda: None
        self.client = TestClient(app)

    def _restore(self):
        identity_router._persona_crud = self._original_persona_crud
        identity_router._persona_feature_crud = self._original_feature_crud

    def test_create_and_list_feature(self):
        persona = _fake_persona()
        FakePersonaCRUD.store[persona.persona_id] = persona

        payload = {
            "persona_id": str(persona.persona_id),
            "feature_code": "tenure_days",
            "feature_name": "Customer Tenure (days)",
            "feature_type": "numeric",
            "numeric_value": "180",
        }
        response = self.client.post("/persona-features/", json=payload)
        self.assertEqual(response.status_code, 201)

        response = self.client.get(f"/persona-features/?persona_id={persona.persona_id}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["feature_code"], "tenure_days")

    def test_get_features_for_persona_via_customer_personas_endpoint(self):
        persona = _fake_persona()
        FakePersonaCRUD.store[persona.persona_id] = persona
        feature_id = uuid.uuid4()
        feature = SimpleNamespace(
            feature_id=feature_id,
            persona_id=persona.persona_id,
            feature_code="source_system_count",
            feature_name="Number of Source Systems",
            feature_type="numeric",
            numeric_value="3",
            text_value=None,
            boolean_value=None,
            source_system=None,
            confidence_score=None,
            computed_at=datetime.now(timezone.utc),
        )
        FakeFeatureCRUD.store[feature_id] = feature

        response = self.client.get(f"/customer-personas/{persona.persona_id}/features")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["feature_code"], "source_system_count")

    def test_get_features_404_when_persona_missing(self):
        response = self.client.get(f"/customer-personas/{uuid.uuid4()}/features")
        self.assertEqual(response.status_code, 404)


class PersonaHistoryRouterTests(unittest.TestCase):
    def setUp(self):
        FakeHistoryCRUD.reset()
        self._original_crud = identity_router._persona_history_crud
        identity_router._persona_history_crud = FakeHistoryCRUD(identity_router.CdpPersonaHistory)

        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)
        self.addCleanup(self._restore)

        app = FastAPI()
        app.include_router(identity_router.persona_history_router)
        app.dependency_overrides[get_db] = lambda: None
        self.client = TestClient(app)

    def _restore(self):
        identity_router._persona_history_crud = self._original_crud

    def test_create_and_get_history_entry(self):
        persona_id = uuid.uuid4()
        payload = {
            "persona_id": str(persona_id),
            "old_persona_name": "Cautious Retail Shopper #aaa111",
            "new_persona_name": "Savvy Retail Shopper #4f2a9c",
            "old_score": "40.00",
            "new_score": "72.50",
            "change_reason": "Recomputed after CIR resolution batch",
            "model_version": "persona-engine-v1",
        }
        response = self.client.post("/persona-history/", json=payload)
        self.assertEqual(response.status_code, 201)
        history_id = response.json()["history_id"]

        response = self.client.get(f"/persona-history/{history_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["new_persona_name"], "Savvy Retail Shopper #4f2a9c")

    def test_get_history_entry_not_found(self):
        response = self.client.get(f"/persona-history/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)


class MasterProfilePersonaEndpointTests(unittest.TestCase):
    """Covers GET /master-profiles/{id}/persona and
    GET /master-profiles/{id}/persona-history."""

    def setUp(self):
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        app = FastAPI()
        app.include_router(identity_router.master_profiles_router)
        self.app = app

    def test_get_current_persona_404_when_profile_missing(self):
        session = _FakeGetExecSession()
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.get(f"/master-profiles/{uuid.uuid4()}/persona")

        self.assertEqual(response.status_code, 404)

    def test_get_current_persona_404_when_no_persona_computed_yet(self):
        master_id = uuid.uuid4()
        profile = SimpleNamespace(master_profile_id=master_id, current_persona_id=None)
        session = _FakeGetExecSession(get_results={(identity_router.CdpMasterProfile, master_id): profile})
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.get(f"/master-profiles/{master_id}/persona")

        self.assertEqual(response.status_code, 404)

    def test_get_current_persona_returns_persona(self):
        master_id = uuid.uuid4()
        persona_id = uuid.uuid4()
        profile = SimpleNamespace(master_profile_id=master_id, current_persona_id=persona_id)
        persona = _fake_persona(persona_id=persona_id, master_profile_id=master_id)
        session = _FakeGetExecSession(
            get_results={
                (identity_router.CdpMasterProfile, master_id): profile,
                (identity_router.CdpCustomerPersona, persona_id): persona,
            }
        )
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.get(f"/master-profiles/{master_id}/persona")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["persona_id"], str(persona_id))

    def test_persona_history_404_when_profile_missing(self):
        session = _FakeGetExecSession()
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.get(f"/master-profiles/{uuid.uuid4()}/persona-history")

        self.assertEqual(response.status_code, 404)

    def test_persona_history_returns_rows(self):
        master_id = uuid.uuid4()
        profile = SimpleNamespace(master_profile_id=master_id, current_persona_id=None)
        history_id = uuid.uuid4()
        history_row = SimpleNamespace(
            history_id=history_id,
            persona_id=uuid.uuid4(),
            old_persona_name="Cautious Retail Shopper #aaa111",
            new_persona_name="Savvy Retail Shopper #4f2a9c",
            old_score="40.00",
            new_score="72.50",
            change_reason="Recomputed after CIR resolution batch",
            model_version="persona-engine-v1",
            changed_at=datetime.now(timezone.utc),
        )
        session = _FakeGetExecSession(
            get_results={(identity_router.CdpMasterProfile, master_id): profile},
            execute_rows=[history_row],
        )
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.get(f"/master-profiles/{master_id}/persona-history")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["new_persona_name"], "Savvy Retail Shopper #4f2a9c")


if __name__ == "__main__":
    unittest.main()
