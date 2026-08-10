"""Unit tests for the Customer Persona Resolution ("identity understanding")
endpoints in core.routers.persona_api: customer_personas_router,
persona_features_router, persona_score_details_router, persona_history_router,
plus master_profiles_router's GET /{id}/persona and GET /{id}/persona-history.

All CRUD routers here are tested by mocking core.routers.persona_api.PersonaRepository
with an in-memory fake (same pattern as test_segment_router.py's
SegmentMatchedProfilesTests), no real PostgreSQL instance required.
"""

import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.routers.identity_api as identity_router
import core.routers.persona_api as persona_router
from core.database import get_db

DEMO_TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


# ---------------------------------------------------------------------------
# In-memory PersonaRepository fake
# ---------------------------------------------------------------------------


class FakePersonaRepository:
    """Stands in for core.repositories.persona_repository.PersonaRepository:
    an in-memory dict-backed store instead of a real database, so the
    persona routers' HTTP-level wiring (status codes, request/response
    schemas, filters) can be tested without SQLAlchemy/PostgreSQL."""

    persona_store: dict[uuid.UUID, SimpleNamespace] = {}
    archetype_store: dict[uuid.UUID, SimpleNamespace] = {}
    master_profile_store: dict[uuid.UUID, SimpleNamespace] = {}
    feature_store: dict[uuid.UUID, SimpleNamespace] = {}
    score_detail_store: dict[uuid.UUID, SimpleNamespace] = {}
    history_store: dict[uuid.UUID, SimpleNamespace] = {}
    last_list_kwargs: dict[str, Any] = {}
    last_analytics_kwargs: dict[str, Any] = {}
    analytics_summary_return: dict[str, Any] = {}

    def __init__(self, session=None):
        self.session = session

    @classmethod
    def reset(cls):
        cls.persona_store = {}
        cls.archetype_store = {}
        cls.master_profile_store = {}
        cls.feature_store = {}
        cls.score_detail_store = {}
        cls.history_store = {}
        cls.last_list_kwargs = {}
        cls.last_analytics_kwargs = {}
        cls.analytics_summary_return = {}

    # --- Persona Archetypes ---

    def list_archetypes(self, tenant_id=None, domain=None, is_active=None, skip=0, limit=100):
        filters = {"tenant_id": tenant_id, "domain": domain, "is_active": is_active}
        results = list(FakePersonaRepository.archetype_store.values())
        for field, value in filters.items():
            if value is not None:
                results = [r for r in results if getattr(r, field, None) == value]
        return results[skip : skip + limit]

    def get_archetype(self, persona_archetype_id: uuid.UUID) -> Optional[SimpleNamespace]:
        return FakePersonaRepository.archetype_store.get(persona_archetype_id)

    def create_archetype(self, payload: dict[str, Any]) -> SimpleNamespace:
        obj = SimpleNamespace(
            persona_archetype_id=uuid.uuid4(),
            matched_profile_count=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            **payload,
        )
        FakePersonaRepository.archetype_store[obj.persona_archetype_id] = obj
        return obj

    def update_archetype(self, persona_archetype_id: uuid.UUID, updates: dict[str, Any]) -> SimpleNamespace:
        obj = FakePersonaRepository.archetype_store[persona_archetype_id]
        for field, value in updates.items():
            setattr(obj, field, value)
        return obj

    def delete_archetype(self, persona_archetype_id: uuid.UUID) -> None:
        FakePersonaRepository.archetype_store.pop(persona_archetype_id, None)

    def list_master_profiles_by_archetype(self, persona_archetype_id, tenant_id=None, page=1, page_size=100) -> dict:
        matching_master_ids = {
            p.master_profile_id
            for p in FakePersonaRepository.persona_store.values()
            if getattr(p, "persona_archetype_id", None) == persona_archetype_id and getattr(p, "is_active", False)
        }
        items = [
            mp
            for mp in FakePersonaRepository.master_profile_store.values()
            if mp.master_profile_id in matching_master_ids
            and (tenant_id is None or getattr(mp, "tenant_id", None) == tenant_id)
        ]
        total = len(items)
        page = max(1, page)
        page_size = max(1, page_size)
        offset = (page - 1) * page_size
        page_items = items[offset : offset + page_size]
        total_pages = max(1, (total + page_size - 1) // page_size)
        return {
            "items": page_items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            },
        }

    # --- Customer Personas ---

    def list_personas(
        self,
        tenant_id=None,
        domain=None,
        master_profile_id=None,
        persona_archetype_id=None,
        is_active=None,
        skip=0,
        limit=100,
    ):
        filters = {
            "tenant_id": tenant_id,
            "domain": domain,
            "master_profile_id": master_profile_id,
            "persona_archetype_id": persona_archetype_id,
            "is_active": is_active,
        }
        FakePersonaRepository.last_list_kwargs = {k: v for k, v in filters.items() if v is not None}
        results = list(FakePersonaRepository.persona_store.values())
        for field, value in filters.items():
            if value is not None:
                results = [r for r in results if getattr(r, field, None) == value]
        return results[skip : skip + limit]

    def get_persona(self, persona_id: uuid.UUID) -> Optional[SimpleNamespace]:
        return FakePersonaRepository.persona_store.get(persona_id)

    def get_master_profile_by_persona_id(self, persona_id: uuid.UUID) -> Optional[SimpleNamespace]:
        persona = FakePersonaRepository.persona_store.get(persona_id)
        if persona is None:
            return None
        master_profile_id = getattr(persona, "master_profile_id", None)
        if master_profile_id is None:
            return None
        return FakePersonaRepository.master_profile_store.get(master_profile_id)

    def list_master_profiles_by_persona_category(
        self,
        persona_category: str,
        tenant_id=None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict:
        matching_master_ids = {
            p.master_profile_id
            for p in FakePersonaRepository.persona_store.values()
            if getattr(p, "persona_category", None) == persona_category and getattr(p, "is_active", False)
        }
        items = [
            mp
            for mp in FakePersonaRepository.master_profile_store.values()
            if mp.master_profile_id in matching_master_ids
            and (tenant_id is None or getattr(mp, "tenant_id", None) == tenant_id)
        ]
        total = len(items)
        page = max(1, page)
        page_size = max(1, page_size)
        offset = (page - 1) * page_size
        page_items = items[offset : offset + page_size]
        total_pages = max(1, (total + page_size - 1) // page_size)
        return {
            "items": page_items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            },
        }

    def create_persona(self, payload: dict[str, Any]) -> SimpleNamespace:
        obj = SimpleNamespace(
            persona_id=uuid.uuid4(),
            computed_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            **payload,
        )
        FakePersonaRepository.persona_store[obj.persona_id] = obj
        return obj

    def update_persona(self, persona_id: uuid.UUID, updates: dict[str, Any]) -> SimpleNamespace:
        obj = FakePersonaRepository.persona_store[persona_id]
        for field, value in updates.items():
            setattr(obj, field, value)
        return obj

    def delete_persona(self, persona_id: uuid.UUID) -> None:
        FakePersonaRepository.persona_store.pop(persona_id, None)

    def get_analytics_summary(self, tenant_id=None, domain=None, is_active=None, days=90) -> dict[str, Any]:
        FakePersonaRepository.last_analytics_kwargs = {
            "tenant_id": tenant_id,
            "domain": domain,
            "is_active": is_active,
            "days": days,
        }
        return FakePersonaRepository.analytics_summary_return

    # --- Persona Features ---

    def list_persona_features(self, persona_id=None, skip=0, limit=100):
        results = list(FakePersonaRepository.feature_store.values())
        if persona_id is not None:
            results = [r for r in results if r.persona_id == persona_id]
        return results[skip : skip + limit]

    def get_persona_feature(self, feature_id: uuid.UUID) -> Optional[SimpleNamespace]:
        return FakePersonaRepository.feature_store.get(feature_id)

    def create_persona_feature(self, payload: dict[str, Any]) -> SimpleNamespace:
        obj = SimpleNamespace(feature_id=uuid.uuid4(), computed_at=datetime.now(timezone.utc), **payload)
        FakePersonaRepository.feature_store[obj.feature_id] = obj
        return obj

    # --- Persona Score Details ---

    def list_persona_score_details(self, persona_id=None, skip=0, limit=100):
        results = list(FakePersonaRepository.score_detail_store.values())
        if persona_id is not None:
            results = [r for r in results if r.persona_id == persona_id]
        return results[skip : skip + limit]

    def get_persona_score_detail(self, score_id: uuid.UUID) -> Optional[SimpleNamespace]:
        return FakePersonaRepository.score_detail_store.get(score_id)

    def create_persona_score_detail(self, payload: dict[str, Any]) -> SimpleNamespace:
        obj = SimpleNamespace(score_id=uuid.uuid4(), created_at=datetime.now(timezone.utc), **payload)
        FakePersonaRepository.score_detail_store[obj.score_id] = obj
        return obj

    # --- Persona History ---

    def list_persona_history(self, persona_id=None, skip=0, limit=100):
        results = list(FakePersonaRepository.history_store.values())
        if persona_id is not None:
            results = [r for r in results if r.persona_id == persona_id]
        return results[skip : skip + limit]

    def get_persona_history(self, history_id: uuid.UUID) -> Optional[SimpleNamespace]:
        return FakePersonaRepository.history_store.get(history_id)

    def create_persona_history(self, payload: dict[str, Any]) -> SimpleNamespace:
        obj = SimpleNamespace(history_id=uuid.uuid4(), changed_at=datetime.now(timezone.utc), **payload)
        FakePersonaRepository.history_store[obj.history_id] = obj
        return obj


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
        "persona_archetype_id": str(uuid.uuid4()),
        "match_score": "0.9000",
        "persona_score": "72.50",
        "lifecycle_stage": "customer",
        "customer_value_tier": "high_value",
        "risk_level": "low",
        "next_best_action": "Offer a loyalty upsell or premium tier upgrade.",
    }
    payload.update(overrides)
    return payload


def _fake_persona(**overrides) -> SimpleNamespace:
    defaults = {
        "persona_id": uuid.uuid4(),
        "tenant_id": DEMO_TENANT_ID,
        "domain": "retail",
        "master_profile_id": uuid.uuid4(),
        "persona_archetype_id": uuid.uuid4(),
        # persona_category is not a real column on the match row in
        # production (it lives on cdp_persona_archetypes), but is kept here
        # on the fake in-memory object purely so this test's category
        # drill-down assertions can filter on it without a real join.
        "persona_category": "High Value Retail Shopper",
        "match_score": "0.9000",
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
        "computed_version": 1,
        "is_active": True,
        "computed_at": datetime.now(timezone.utc),
        "expires_at": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_archetype(**overrides) -> SimpleNamespace:
    defaults = {
        "persona_archetype_id": uuid.uuid4(),
        "tenant_id": DEMO_TENANT_ID,
        "domain": "retail",
        "persona_code": "retail_gen_z_sneaker_collector_2026h2",
        "persona_name": "Gen Z Sneaker Collector",
        "persona_category": "Champion",
        "persona_summary": "ICP for the H2 2026 sneaker drop campaign.",
        "llm_provider": "seed-script",
        "llm_model": "icp-catalog-v1",
        "centroid_behavior_score": "85.00",
        "centroid_engagement_score": "90.00",
        "centroid_financial_score": "55.00",
        "centroid_loyalty_score": "65.00",
        "centroid_relationship_score": "55.00",
        "centroid_risk_score": "20.00",
        "matched_profile_count": 0,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_master_profile(**overrides) -> SimpleNamespace:
    defaults = {
        "master_profile_id": uuid.uuid4(),
        "tenant_id": DEMO_TENANT_ID,
        "user_id": None,
        "domain": "retail",
        "full_name": "Jane Doe",
        "first_name": "Jane",
        "last_name": "Doe",
        "is_hashed": False,
        "email": "jane@example.com",
        "phone_number": None,
        "secondary_emails": [],
        "secondary_phones": [],
        "date_of_birth": None,
        "gender": None,
        "address": None,
        "company_name": None,
        "external_ids": {},
        "device_ids": [],
        "advertising_ids": [],
        "cookie_ids": [],
        "push_tokens": {},
        "acquisition_source": None,
        "acquisition_campaign": None,
        "current_persona_id": None,
        "persona_name": None,
        "segmentation_tags": [],
        "communication_preferences": {},
        "attributes": {},
        "source_systems": [],
        "first_seen_raw_profile_id": None,
        "customer_since": None,
        "last_activity_at": None,
        "preferred_channel": None,
        "lifecycle_stage": None,
        "persona_summary": None,
        "lead_conversion_probability": None,
        "lead_grade": None,
        "churn_probability": None,
        "churn_risk_tier": None,
        "historical_clv": 0,
        "predictive_clv": None,
        "clv_segment": None,
        "engagement_score": None,
        "latest_nps_score": None,
        "average_csat": None,
        "overall_sentiment_score": None,
        "profile_completeness_score": None,
        "identity_confidence_score": None,
        "model_versions": {},
        "scores_updated_at": None,
        "linked_raw_profile_count": 0,
        "status_code": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _patch_persona_repository(test_case: unittest.TestCase) -> None:
    """Shared setUp helper: resets the fake store and patches
    core.routers.persona_api.PersonaRepository so every `PersonaRepository(db)`
    call site in the router returns a FakePersonaRepository instance."""
    FakePersonaRepository.reset()
    repo_patcher = patch("core.routers.persona_api.PersonaRepository", side_effect=FakePersonaRepository)
    repo_patcher.start()
    test_case.addCleanup(repo_patcher.stop)

    cache_patcher = patch("core.cache.get_redis_client", return_value=None)
    cache_patcher.start()
    test_case.addCleanup(cache_patcher.stop)


class PersonaArchetypesRouterTests(unittest.TestCase):
    """Covers /persona/archetypes CRUD + the matched-profiles drill-down.

    Wires up persona_archetypes_router AND customer_personas_router together
    (in the SAME order app.py does, via all_persona_routers) so this is a
    real regression test for the routing bug where GET /persona/archetypes
    (no trailing slash) was being swallowed by customer_personas_router's
    GET /persona/{persona_id} catch-all, producing a 422 uuid_parsing error
    with persona_id="archetypes"."""

    def setUp(self):
        _patch_persona_repository(self)

        self._domain_patcher = patch(
            "core.utils.domains.get_active_domain_codes",
            return_value={"retail", "banking", "healthcare", "real_estate", "travel", "media", "education"},
        )
        self._domain_patcher.start()
        self.addCleanup(self._domain_patcher.stop)

        app = FastAPI()
        for router in persona_router.all_persona_routers:
            app.include_router(router)
        app.dependency_overrides[get_db] = lambda: None
        self.client = TestClient(app)

    def test_list_archetypes_without_trailing_slash_is_not_swallowed_by_persona_id_route(self):
        archetype = _fake_archetype()
        FakePersonaRepository.archetype_store[archetype.persona_archetype_id] = archetype

        response = self.client.get("/persona/archetypes")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["persona_archetype_id"], str(archetype.persona_archetype_id))

    def test_list_archetypes_empty(self):
        response = self.client.get("/persona/archetypes")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_create_and_get_archetype(self):
        payload = {
            "tenant_id": str(DEMO_TENANT_ID),
            "domain": "retail",
            "persona_code": "retail_gen_z_sneaker_collector_2026h2",
            "persona_name": "Gen Z Sneaker Collector",
            "persona_category": "Champion",
        }
        response = self.client.post("/persona/archetypes", json=payload)
        self.assertEqual(response.status_code, 201)
        archetype_id = response.json()["persona_archetype_id"]

        response = self.client.get(f"/persona/archetypes/{archetype_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["persona_name"], "Gen Z Sneaker Collector")
        self.assertEqual(response.json()["matched_profile_count"], 0)

    def test_get_archetype_not_found(self):
        response = self.client.get(f"/persona/archetypes/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)

    def test_matched_master_profiles_for_archetype(self):
        archetype = _fake_archetype()
        FakePersonaRepository.archetype_store[archetype.persona_archetype_id] = archetype
        matching_persona = _fake_persona(persona_archetype_id=archetype.persona_archetype_id, is_active=True)
        other_persona = _fake_persona(persona_archetype_id=uuid.uuid4(), is_active=True)
        matching_profile = _fake_master_profile(master_profile_id=matching_persona.master_profile_id)
        other_profile = _fake_master_profile(master_profile_id=other_persona.master_profile_id)
        FakePersonaRepository.persona_store[matching_persona.persona_id] = matching_persona
        FakePersonaRepository.persona_store[other_persona.persona_id] = other_persona
        FakePersonaRepository.master_profile_store[matching_profile.master_profile_id] = matching_profile
        FakePersonaRepository.master_profile_store[other_profile.master_profile_id] = other_profile

        response = self.client.get(f"/persona/archetypes/{archetype.persona_archetype_id}/master-profiles")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["master_profile_id"], str(matching_profile.master_profile_id))

    def test_matched_master_profiles_404_when_archetype_missing(self):
        response = self.client.get(f"/persona/archetypes/{uuid.uuid4()}/master-profiles")
        self.assertEqual(response.status_code, 404)

    def test_update_and_delete_archetype(self):
        archetype = _fake_archetype()
        FakePersonaRepository.archetype_store[archetype.persona_archetype_id] = archetype

        response = self.client.patch(f"/persona/archetypes/{archetype.persona_archetype_id}", json={"is_active": False})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_active"])

        response = self.client.delete(f"/persona/archetypes/{archetype.persona_archetype_id}")
        self.assertEqual(response.status_code, 204)
        self.assertNotIn(archetype.persona_archetype_id, FakePersonaRepository.archetype_store)

    def test_get_customer_persona_by_id_route_still_works_alongside_archetypes(self):
        """Regression guard for the OTHER direction: adding the archetypes
        router must not break GET /persona/{persona_id}."""
        persona = _fake_persona()
        FakePersonaRepository.persona_store[persona.persona_id] = persona

        response = self.client.get(f"/persona/{persona.persona_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["persona_id"], str(persona.persona_id))


class CustomerPersonasRouterTests(unittest.TestCase):
    """Covers the customer personas CRUD (list filters, get 200/404, create,
    patch, delete 204/404, cache invalidation, analytics summary)."""

    def setUp(self):
        _patch_persona_repository(self)

        self._domain_patcher = patch(
            "core.utils.domains.get_active_domain_codes",
            return_value={"retail", "banking", "healthcare", "real_estate", "travel", "media", "education"},
        )
        self._domain_patcher.start()
        self.addCleanup(self._domain_patcher.stop)

        app = FastAPI()
        app.include_router(persona_router.customer_personas_router)
        app.dependency_overrides[get_db] = lambda: None
        self.client = TestClient(app)

    def test_create_persona(self):
        payload = _persona_payload()
        response = self.client.post("/persona/", json=payload)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["persona_archetype_id"], payload["persona_archetype_id"])
        self.assertTrue(body["is_active"])
        self.assertEqual(body["computed_version"], 1)

    def test_create_persona_rejects_invalid_risk_level(self):
        response = self.client.post("/persona/", json=_persona_payload(risk_level="extreme"))
        self.assertEqual(response.status_code, 422)

    def test_create_persona_rejects_invalid_domain(self):
        response = self.client.post("/persona/", json=_persona_payload(domain="finance"))
        self.assertEqual(response.status_code, 422)

    def test_create_persona_invalidates_cache(self):
        with patch("core.routers.persona_api.invalidate_prefix") as mock_invalidate:
            response = self.client.post("/persona/", json=_persona_payload())
        self.assertEqual(response.status_code, 201)
        mock_invalidate.assert_called_once_with("customer_personas")

    def test_list_filters_by_master_profile_id(self):
        master_id = uuid.uuid4()
        matching = _fake_persona(master_profile_id=master_id)
        other = _fake_persona()
        FakePersonaRepository.persona_store[matching.persona_id] = matching
        FakePersonaRepository.persona_store[other.persona_id] = other

        response = self.client.get(f"/persona/list?master_profile_id={master_id}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["persona_id"], str(matching.persona_id))

    def test_list_filters_by_is_active(self):
        active = _fake_persona(is_active=True)
        inactive = _fake_persona(is_active=False)
        FakePersonaRepository.persona_store[active.persona_id] = active
        FakePersonaRepository.persona_store[inactive.persona_id] = inactive

        response = self.client.get("/persona/list?is_active=true")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["persona_id"], str(active.persona_id))

    def test_list_filters_by_domain(self):
        retail_persona = _fake_persona(domain="retail")
        banking_persona = _fake_persona(domain="banking")
        healthcare_persona = _fake_persona(domain="healthcare")
        FakePersonaRepository.persona_store[retail_persona.persona_id] = retail_persona
        FakePersonaRepository.persona_store[banking_persona.persona_id] = banking_persona
        FakePersonaRepository.persona_store[healthcare_persona.persona_id] = healthcare_persona

        response = self.client.get("/persona/list?domain=healthcare")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["persona_id"], str(healthcare_persona.persona_id))

    def test_list_rejects_invalid_domain(self):
        response = self.client.get("/persona/list?domain=finance")
        self.assertEqual(response.status_code, 422)

    def test_get_persona_not_found(self):
        response = self.client.get(f"/persona/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)

    def test_get_master_profile_for_persona(self):
        persona = _fake_persona()
        master_profile = _fake_master_profile(
            master_profile_id=persona.master_profile_id,
            current_persona_id=persona.persona_id,
        )
        FakePersonaRepository.persona_store[persona.persona_id] = persona
        FakePersonaRepository.master_profile_store[master_profile.master_profile_id] = master_profile

        response = self.client.get(f"/persona/{persona.persona_id}/master-profile")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["master_profile_id"], str(master_profile.master_profile_id))
        self.assertEqual(body["full_name"], "Jane Doe")

    def test_get_master_profile_not_found_for_persona(self):
        response = self.client.get(f"/persona/{uuid.uuid4()}/master-profile")
        self.assertEqual(response.status_code, 404)

    def test_list_master_profiles_by_persona_category(self):
        matching_persona = _fake_persona(persona_category="High Value Retail Shopper", is_active=True)
        other_persona = _fake_persona(persona_category="At Risk Customer", is_active=True)
        matching_profile = _fake_master_profile(master_profile_id=matching_persona.master_profile_id)
        other_profile = _fake_master_profile(master_profile_id=other_persona.master_profile_id)
        FakePersonaRepository.persona_store[matching_persona.persona_id] = matching_persona
        FakePersonaRepository.persona_store[other_persona.persona_id] = other_persona
        FakePersonaRepository.master_profile_store[matching_profile.master_profile_id] = matching_profile
        FakePersonaRepository.master_profile_store[other_profile.master_profile_id] = other_profile

        response = self.client.get("/persona/category/High Value Retail Shopper/master-profiles")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["master_profile_id"], str(matching_profile.master_profile_id))
        self.assertEqual(body["pagination"]["total"], 1)

    def test_patch_persona(self):
        persona = _fake_persona()
        FakePersonaRepository.persona_store[persona.persona_id] = persona

        response = self.client.patch(
            f"/persona/{persona.persona_id}", json={"is_active": False, "risk_level": "high"}
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["is_active"])
        self.assertEqual(body["risk_level"], "high")

    def test_patch_persona_not_found(self):
        response = self.client.patch(f"/persona/{uuid.uuid4()}", json={"is_active": False})
        self.assertEqual(response.status_code, 404)

    def test_delete_persona(self):
        persona = _fake_persona()
        FakePersonaRepository.persona_store[persona.persona_id] = persona

        response = self.client.delete(f"/persona/{persona.persona_id}")

        self.assertEqual(response.status_code, 204)
        self.assertNotIn(persona.persona_id, FakePersonaRepository.persona_store)

    def test_delete_persona_not_found(self):
        response = self.client.delete(f"/persona/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)

    def test_get_persona_analytics_summary(self):
        FakePersonaRepository.analytics_summary_return = {
            "total_archetypes": 5,
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

        response = self.client.get("/persona/analytics/summary?domain=healthcare&is_active=true&days=30")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total_personas"], 10)
        self.assertEqual(body["active_personas"], 8)
        self.assertEqual(body["avg_confidence_score"], 0.8123)
        self.assertEqual(FakePersonaRepository.last_analytics_kwargs["domain"], "healthcare")
        self.assertTrue(FakePersonaRepository.last_analytics_kwargs["is_active"])
        self.assertEqual(FakePersonaRepository.last_analytics_kwargs["days"], 30)

    def test_get_persona_analytics_summary_rejects_invalid_domain(self):
        response = self.client.get("/persona/analytics/summary?domain=finance")
        self.assertEqual(response.status_code, 422)


class PersonaFeaturesRouterTests(unittest.TestCase):
    """Covers /persona/features/ list/get/create plus the
    /persona/{persona_id}/features convenience endpoint."""

    def setUp(self):
        _patch_persona_repository(self)

        app = FastAPI()
        app.include_router(persona_router.customer_personas_router)
        app.include_router(persona_router.persona_features_router)
        app.dependency_overrides[get_db] = lambda: None
        self.client = TestClient(app)

    def test_create_and_list_feature(self):
        persona = _fake_persona()
        FakePersonaRepository.persona_store[persona.persona_id] = persona

        payload = {
            "persona_id": str(persona.persona_id),
            "feature_code": "tenure_days",
            "feature_name": "Customer Tenure (days)",
            "feature_type": "numeric",
            "numeric_value": "180",
        }
        response = self.client.post("/persona/features/", json=payload)
        self.assertEqual(response.status_code, 201)

        response = self.client.get(f"/persona/features/?persona_id={persona.persona_id}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["feature_code"], "tenure_days")

    def test_get_features_for_persona_via_customer_personas_endpoint(self):
        persona = _fake_persona()
        FakePersonaRepository.persona_store[persona.persona_id] = persona
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
        FakePersonaRepository.feature_store[feature_id] = feature

        response = self.client.get(f"/persona/{persona.persona_id}/features")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["feature_code"], "source_system_count")

    def test_get_features_404_when_persona_missing(self):
        response = self.client.get(f"/persona/{uuid.uuid4()}/features")
        self.assertEqual(response.status_code, 404)


class PersonaHistoryRouterTests(unittest.TestCase):
    """Covers /persona/history/ create/get."""

    def setUp(self):
        _patch_persona_repository(self)

        app = FastAPI()
        app.include_router(persona_router.persona_history_router)
        app.dependency_overrides[get_db] = lambda: None
        self.client = TestClient(app)

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
        response = self.client.post("/persona/history/", json=payload)
        self.assertEqual(response.status_code, 201)
        history_id = response.json()["history_id"]

        response = self.client.get(f"/persona/history/{history_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["new_persona_name"], "Savvy Retail Shopper #4f2a9c")

    def test_get_history_entry_not_found(self):
        response = self.client.get(f"/persona/history/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)


class MasterProfilePersonaEndpointTests(unittest.TestCase):
    """Covers GET /master-profiles/{id}/persona and
    GET /master-profiles/{id}/persona-history. These hand-written endpoints
    query the DB session directly (db.get/db.execute), not via
    PersonaRepository, so they're exercised with a fake Session instead."""

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
