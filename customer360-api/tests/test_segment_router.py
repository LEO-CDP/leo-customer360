"""Unit tests for the /segments CRUD endpoints (core.routers.segment_api), built
on the generic CRUD router factory (core.routers._generic.build_crud_router)
-- verifies request/response wiring for CdpSegment (create/list/get/update/
delete/count) entirely against an in-memory fake CRUD layer, no real
PostgreSQL instance required.
"""

import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from core.database import get_db
from core.models.segmentation import CdpSegment
from core.routers._generic import build_crud_router
from core.routers.segment_api import (
    _segment_integrity_error_detail,
    _transform_segment_create,
    _transform_segment_update,
    _trigger_segment_recompute_after_create,
    _trigger_segment_recompute_after_update,
)
from core.schemas.segmentation import SegmentCreate, SegmentRead, SegmentUpdate


class FakeSegmentCRUD:
    """Stands in for core.crud.base.CRUDBase(CdpSegment): an in-memory
    dict-backed store instead of a real database, so the router's HTTP-level
    wiring (status codes, request/response schemas, filters) can be tested
    without SQLAlchemy/PostgreSQL."""

    store: dict[uuid.UUID, SimpleNamespace] = {}
    last_list_kwargs: dict[str, Any] = {}
    last_count_kwargs: dict[str, Any] = {}
    integrity_error: Optional[IntegrityError] = None

    def __init__(self, model):
        self.model = model

    @classmethod
    def reset(cls):
        cls.store = {}
        cls.last_list_kwargs = {}
        cls.last_count_kwargs = {}
        cls.integrity_error = None

    def list(self, db, *, skip=0, limit=100, **filters):
        FakeSegmentCRUD.last_list_kwargs = filters
        return list(FakeSegmentCRUD.store.values())

    def count(self, db, **filters):
        FakeSegmentCRUD.last_count_kwargs = filters
        return len(FakeSegmentCRUD.store)

    def get(self, db, pk: uuid.UUID) -> Optional[SimpleNamespace]:
        return FakeSegmentCRUD.store.get(pk)

    def create(self, db, obj_in: dict[str, Any]) -> SimpleNamespace:
        if FakeSegmentCRUD.integrity_error is not None:
            raise FakeSegmentCRUD.integrity_error
        obj = SimpleNamespace(
            segment_id=uuid.uuid4(),
            status_code=1,
            member_count=0,
            last_computed_at=None,
            created_at=None,
            updated_at=None,
            **obj_in,
        )
        FakeSegmentCRUD.store[obj.segment_id] = obj
        return obj

    def update(self, db, db_obj: SimpleNamespace, obj_in: dict[str, Any]) -> SimpleNamespace:
        if FakeSegmentCRUD.integrity_error is not None:
            raise FakeSegmentCRUD.integrity_error
        for field, value in obj_in.items():
            setattr(db_obj, field, value)
        return db_obj

    def delete(self, db, db_obj: SimpleNamespace) -> None:
        FakeSegmentCRUD.store.pop(db_obj.segment_id, None)


class _FakeDatabaseError(Exception):
    def __init__(self, constraint_name: str):
        super().__init__("duplicate key")
        self.diag = SimpleNamespace(constraint_name=constraint_name)


def _build_test_app(*, create_hook=None, update_hook=None, db=None) -> FastAPI:
    with patch("core.routers._generic.CRUDBase", FakeSegmentCRUD):
        router = build_crud_router(
            model=CdpSegment,
            pk_field="segment_id",
            pk_type=uuid.UUID,
            create_schema=SegmentCreate,
            update_schema=SegmentUpdate,
            read_schema=SegmentRead,
            prefix="/segments",
            tags=["Segmentation"],
            create_validator=lambda db, payload: __import__("core.utils.domains", fromlist=["validate_domain_value"]).validate_domain_value(
                db, payload.get("domain"), allow_all=True
            ),
            update_validator=lambda db, payload: __import__("core.utils.domains", fromlist=["validate_domain_value"]).validate_domain_value(
                db, payload.get("domain"), allow_all=True
            ),
            create_transform=_transform_segment_create,
            update_transform=_transform_segment_update,
            integrity_error_detail=_segment_integrity_error_detail,
            create_hook=create_hook,
            update_hook=update_hook,
        )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    return app


def _segment_payload(**overrides) -> dict[str, Any]:
    payload = {
        "tenant_id": str(uuid.uuid4()),
        "domain": "retail",
        "segment_tag": "gen_z_shopper",
        "segment_name": "Gen Z Shoppers",
        "description": "Profiles under 25 with 3+ purchases in the last quarter.",
        "json_rules": {"condition": "AND", "rules": [{"field": "age", "operator": "less", "value": 25}]},
        "sql_rules": "age < 25",
        "processed_by": "human",
    }
    payload.update(overrides)
    return payload


class SegmentCrudTests(unittest.TestCase):
    def setUp(self):
        FakeSegmentCRUD.reset()
        # Avoid any real Redis connection attempts from @cache_response.
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        self._domain_patcher = patch(
            "core.utils.domains.get_active_domain_codes",
            return_value={"retail", "banking", "healthcare", "real_estate", "travel", "media", "education"},
        )
        self._domain_patcher.start()
        self.addCleanup(self._domain_patcher.stop)
        self.client = TestClient(_build_test_app())

    def test_create_segment_returns_201_with_generated_id(self):
        response = self.client.post("/segments/", json=_segment_payload())

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn("segment_id", body)
        self.assertEqual(body["segment_tag"], "gen_z_shopper")
        self.assertEqual(body["segment_name"], "Gen Z Shoppers")
        self.assertEqual(body["processed_by"], "human")
        self.assertEqual(body["status_code"], 1)

    def test_create_duplicate_segment_tag_returns_409(self):
        db = Mock()
        FakeSegmentCRUD.integrity_error = IntegrityError(
            "INSERT", {}, _FakeDatabaseError("uq_cdp_segments_tenant_tag")
        )
        client = TestClient(_build_test_app(db=db))

        response = client.post("/segments/", json=_segment_payload())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "A segment with this tag already exists in this workspace.")
        db.rollback.assert_called_once()

    def test_create_hook_runs_after_segment_is_persisted(self):
        create_hook = Mock()
        client = TestClient(_build_test_app(create_hook=create_hook))
        payload = _segment_payload()

        response = client.post("/segments/", json=payload)

        self.assertEqual(response.status_code, 201)
        create_hook.assert_called_once()
        self.assertEqual(create_hook.call_args.args[0].tenant_id, uuid.UUID(payload["tenant_id"]))

    def test_update_hook_runs_after_segment_is_persisted(self):
        update_hook = Mock()
        client = TestClient(_build_test_app(update_hook=update_hook))
        payload = _segment_payload()
        created = client.post("/segments/", json=payload).json()

        response = client.patch(
            f"/segments/{created['segment_id']}",
            json={"segment_name": "Updated Name"},
        )

        self.assertEqual(response.status_code, 200)
        update_hook.assert_called_once()
        self.assertEqual(update_hook.call_args.args[0].tenant_id, uuid.UUID(payload["tenant_id"]))

    def test_create_segment_translates_relative_datetime_and_generates_sql(self):
        payload = _segment_payload(
            json_rules={
                "condition": "AND",
                "rules": [{"field": "last_activity_at", "operator": "greater_or_equal", "value": "-5 days"}],
            },
            sql_rules="(last_activity_at >= '-5 days')",
        )

        response = self.client.post("/segments/", json=payload)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        expected_rules = "(last_activity_at >= (now() - INTERVAL '5 days'))"
        self.assertEqual(body["sql_rules"], expected_rules)
        self.assertEqual(
            body["final_generated_sql"],
            "SELECT master_profile_id FROM customer360.cdp_master_profiles "
            f"WHERE tenant_id = '{payload['tenant_id']}'::uuid AND {expected_rules}",
        )

    def test_create_segment_defaults_processed_by_to_human(self):
        payload = _segment_payload()
        del payload["processed_by"]

        response = self.client.post("/segments/", json=payload)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["processed_by"], "human")

    def test_create_segment_accepts_ai_agent_as_processed_by(self):
        response = self.client.post("/segments/", json=_segment_payload(processed_by="ai_agent"))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["processed_by"], "ai_agent")

    def test_create_segment_accepts_healthcare_domain(self):
        response = self.client.post("/segments/", json=_segment_payload(domain="healthcare"))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["domain"], "healthcare")

    def test_create_segment_rejects_invalid_processed_by(self):
        response = self.client.post("/segments/", json=_segment_payload(processed_by="robot"))

        self.assertEqual(response.status_code, 422)

    def test_create_segment_rejects_invalid_domain(self):
        response = self.client.post("/segments/", json=_segment_payload(domain="not_a_domain"))

        self.assertEqual(response.status_code, 422)

    def test_get_segment_by_id_after_create(self):
        created = self.client.post("/segments/", json=_segment_payload()).json()

        response = self.client.get(f"/segments/{created['segment_id']}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["segment_id"], created["segment_id"])

    def test_get_missing_segment_returns_404(self):
        response = self.client.get(f"/segments/{uuid.uuid4()}")

        self.assertEqual(response.status_code, 404)

    def test_list_segments_returns_all_created(self):
        self.client.post("/segments/", json=_segment_payload(segment_tag="tag_a"))
        self.client.post("/segments/", json=_segment_payload(segment_tag="tag_b"))

        response = self.client.get("/segments/")

        self.assertEqual(response.status_code, 200)
        tags = {item["segment_tag"] for item in response.json()}
        self.assertEqual(tags, {"tag_a", "tag_b"})

    def test_list_segments_passes_through_tenant_id_filter(self):
        tenant_id = str(uuid.uuid4())

        response = self.client.get(f"/segments/?tenant_id={tenant_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(FakeSegmentCRUD.last_list_kwargs, {"tenant_id": uuid.UUID(tenant_id)})

    def test_count_segments_reflects_number_created(self):
        self.client.post("/segments/", json=_segment_payload(segment_tag="tag_a"))
        self.client.post("/segments/", json=_segment_payload(segment_tag="tag_b"))

        response = self.client.get("/segments/count")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"count": 2})

    def test_update_segment_partially_changes_only_given_fields(self):
        created = self.client.post("/segments/", json=_segment_payload()).json()

        response = self.client.patch(
            f"/segments/{created['segment_id']}",
            json={"segment_name": "Updated Name", "is_active": False},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["segment_name"], "Updated Name")
        self.assertFalse(body["is_active"])
        # Untouched fields must survive the partial update.
        self.assertEqual(body["segment_tag"], "gen_z_shopper")
        self.assertEqual(body["description"], created["description"])

    def test_update_segment_regenerates_sql_when_rules_change(self):
        created = self.client.post("/segments/", json=_segment_payload()).json()
        updated_rules = "(last_activity_at >= '-5 days')"

        response = self.client.patch(
            f"/segments/{created['segment_id']}",
            json={"sql_rules": updated_rules},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        normalized_rules = "(last_activity_at >= (now() - INTERVAL '5 days'))"
        self.assertEqual(body["sql_rules"], normalized_rules)
        self.assertEqual(
            body["final_generated_sql"],
            "SELECT master_profile_id FROM customer360.cdp_master_profiles "
            f"WHERE tenant_id = '{created['tenant_id']}'::uuid AND {normalized_rules}",
        )

    def test_update_segment_clearing_rules_clears_generated_sql(self):
        created = self.client.post("/segments/", json=_segment_payload()).json()

        response = self.client.patch(
            f"/segments/{created['segment_id']}",
            json={"sql_rules": None},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["final_generated_sql"])

    def test_update_missing_segment_returns_404(self):
        response = self.client.patch(f"/segments/{uuid.uuid4()}", json={"segment_name": "Nope"})

        self.assertEqual(response.status_code, 404)

    def test_delete_segment_removes_it(self):
        created = self.client.post("/segments/", json=_segment_payload()).json()

        delete_response = self.client.delete(f"/segments/{created['segment_id']}")
        get_response = self.client.get(f"/segments/{created['segment_id']}")

        self.assertEqual(delete_response.status_code, 204)
        self.assertEqual(get_response.status_code, 404)

    def test_delete_missing_segment_returns_404(self):
        response = self.client.delete(f"/segments/{uuid.uuid4()}")

        self.assertEqual(response.status_code, 404)

    @patch("core.routers.segment_api.dagster_client.segmentation.create", return_value="run-create")
    def test_create_trigger_submits_tenant_scoped_dagster_job(self, mock_trigger):
        tenant_id = uuid.uuid4()
        segment = SimpleNamespace(segment_id=uuid.uuid4(), tenant_id=tenant_id)

        _trigger_segment_recompute_after_create(segment)

        mock_trigger.assert_called_once_with(
            tenant_id=str(tenant_id),
            segment_id=str(segment.segment_id),
        )

    @patch("core.routers.segment_api.dagster_client.segmentation.update", return_value="run-update")
    def test_update_trigger_submits_tenant_scoped_dagster_job(self, mock_trigger):
        tenant_id = uuid.uuid4()
        segment = SimpleNamespace(segment_id=uuid.uuid4(), tenant_id=tenant_id)

        _trigger_segment_recompute_after_update(segment)

        mock_trigger.assert_called_once_with(
            tenant_id=str(tenant_id),
            segment_id=str(segment.segment_id),
        )


class _FakeRows:
    """Stands in for a SQLAlchemy CursorResult supporting `.mappings().all()`."""

    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeScalarOne:
    """Stands in for a SQLAlchemy CursorResult supporting `.scalar_one()`."""

    def __init__(self, value: Any):
        self._value = value

    def scalar_one(self):
        return self._value


class _FakeExecSession:
    """Minimal Session double recording every execute() call, returning a
    single canned result (this app-level test never issues more than one
    query per request)."""

    def __init__(self, result: Any = None):
        self.result = result
        self.executed: list[tuple[str, Optional[dict[str, Any]]]] = []

    def execute(self, stmt: Any, params: Optional[dict[str, Any]] = None) -> Any:
        self.executed.append((str(stmt), params))
        return self.result


class SegmentMatchedProfilesTests(unittest.TestCase):
    """Tests the real core.routers.segment_api.segments_router (including the
    hand-written matched-profiles endpoints, not just the generic CRUD
    routes) with a mocked SegmentRepository."""

    def setUp(self):
        import core.routers.segment_api as segment_router_module

        self.segment_router_module = segment_router_module
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        self.app = FastAPI()
        self.app.include_router(segment_router_module.segments_router)

    def _client_for(self, fake_segment: Optional[SimpleNamespace], fake_session: _FakeExecSession) -> TestClient:
        self.app.dependency_overrides[get_db] = lambda: fake_session
        
        # Mock SegmentRepository to use the fake_session for queries
        def mock_repo_factory(db):
            # Import the real SegmentRepository
            from core.repositories.segment_respository import SegmentRepository
            
            # Create a real repository instance with the fake_session
            repo = SegmentRepository(db)
            
            # Override get_segment to return fake_segment
            original_get_segment = repo.get_segment
            repo.get_segment = lambda seg_id: fake_segment
            
            return repo
        
        repo_patcher = patch(
            "core.routers.segment_api.SegmentRepository",
            side_effect=mock_repo_factory
        )
        repo_patcher.start()
        self.addCleanup(repo_patcher.stop)
        return TestClient(self.app)

    def test_matched_profiles_404_for_missing_segment(self):
        client = self._client_for(None, _FakeExecSession())

        response = client.get(f"/segments/{uuid.uuid4()}/matched-profiles")

        self.assertEqual(response.status_code, 404)

    def test_matched_profiles_count_404_for_missing_segment(self):
        client = self._client_for(None, _FakeExecSession())

        response = client.get(f"/segments/{uuid.uuid4()}/matched-profiles/count")

        self.assertEqual(response.status_code, 404)

    def test_matched_profiles_returns_empty_list_when_no_sql_rules(self):
        segment = SimpleNamespace(segment_id=uuid.uuid4(), tenant_id=uuid.uuid4(), sql_rules=None)
        client = self._client_for(segment, _FakeExecSession())

        response = client.get(f"/segments/{segment.segment_id}/matched-profiles")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_matched_profiles_count_returns_zero_when_no_sql_rules(self):
        segment = SimpleNamespace(segment_id=uuid.uuid4(), tenant_id=uuid.uuid4(), sql_rules=None)
        client = self._client_for(segment, _FakeExecSession())

        response = client.get(f"/segments/{segment.segment_id}/matched-profiles/count")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"count": 0})

    def test_matched_profiles_count_executes_tenant_scoped_query(self):
        tenant_id = uuid.uuid4()
        segment = SimpleNamespace(
            segment_id=uuid.uuid4(),
            tenant_id=tenant_id,
            sql_rules="churn_risk_tier IN ('high', 'critical')",
        )
        fake_session = _FakeExecSession(result=_FakeScalarOne(7))
        client = self._client_for(segment, fake_session)

        response = client.get(f"/segments/{segment.segment_id}/matched-profiles/count")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"count": 7})
        sql, params = fake_session.executed[0]
        self.assertIn("cdp_master_profiles", sql)
        self.assertIn("cdp_domain_profiles", sql)
        self.assertIn("churn_risk_tier IN ('high', 'critical')", sql)
        self.assertEqual(params["tenant_id"], str(tenant_id))

    def test_matched_profiles_returns_rows_from_query(self):
        tenant_id = uuid.uuid4()
        profile_id = str(uuid.uuid4())
        segment = SimpleNamespace(segment_id=uuid.uuid4(), tenant_id=tenant_id, sql_rules="predictive_clv > 1000")
        row = {
            "master_profile_id": profile_id,
            "tenant_id": str(tenant_id),
            "domain": "retail",
            "is_hashed": False,
            "secondary_emails": [],
            "secondary_phones": [],
            "external_ids": {},
            "device_ids": [],
            "advertising_ids": [],
            "cookie_ids": [],
            "push_tokens": {},
            "account_numbers": [],
            "attributes": {},
            "source_systems": [],
            "model_versions": {},
            "historical_clv": 0.0,
            "status_code": 1,
        }
        fake_session = _FakeExecSession(result=_FakeRows([row]))
        client = self._client_for(segment, fake_session)

        response = client.get(f"/segments/{segment.segment_id}/matched-profiles")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["master_profile_id"], profile_id)

    def test_matched_profiles_rejects_unsafe_sql_rules_at_execution_time(self):
        """Defense-in-depth: even if unsafe sql_rules somehow ended up on a
        row (e.g. seeded outside the API), execution-time validation must
        still reject it with a clean 400 rather than running it."""
        segment = SimpleNamespace(
            segment_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            sql_rules="1=1; DROP TABLE cdp_master_profiles;",
        )
        client = self._client_for(segment, _FakeExecSession())

        response = client.get(f"/segments/{segment.segment_id}/matched-profiles")

        self.assertEqual(response.status_code, 400)


class SegmentRecomputeTests(unittest.TestCase):
    """Tests the hand-written POST /segments/{id}/recompute endpoint
    (core.routers.segment_api.recompute_segment), mocking out SegmentRepository
    so these stay unit tests (no real PostgreSQL)."""

    def setUp(self):
        import core.routers.segment_api as segment_router_module

        self.segment_router_module = segment_router_module
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        self.app = FastAPI()
        self.app.include_router(segment_router_module.segments_router)
        self.app.dependency_overrides[get_db] = lambda: None

    def _client_for(
        self,
        fake_segment: Optional[SimpleNamespace],
        tenant_id: Optional[str] = None,
    ) -> TestClient:
        request_tenant_id = tenant_id or (str(fake_segment.tenant_id) if fake_segment else None)

        # Mock SegmentRepository to return fake_segment
        def mock_repo_factory(db):
            repo = SimpleNamespace()
            repo.get_segment = lambda seg_id: fake_segment
            
            def mock_recompute(seg_id):
                if fake_segment is None:
                    raise ValueError(f"CdpSegment '{seg_id}' not found")
                if not getattr(fake_segment, 'sql_rules', None):
                    raise ValueError("Segment has no sql_rules to compute")
                return {
                    "segment_id": str(fake_segment.segment_id),
                    "member_count": getattr(fake_segment, 'member_count', 0),
                    "last_computed_at": getattr(fake_segment, 'last_computed_at', None),
                }
            
            repo.recompute_membership = mock_recompute
            return repo
        
        repo_patcher = patch(
            "core.routers.segment_api.SegmentRepository",
            side_effect=mock_repo_factory
        )
        repo_patcher.start()
        self.addCleanup(repo_patcher.stop)

        @self.app.middleware("http")
        async def _inject_tenant(request, call_next):
            if request_tenant_id is not None:
                request.state.tenant_id = request_tenant_id
            return await call_next(request)

        return TestClient(self.app)

    def test_recompute_404_for_missing_segment(self):
        client = self._client_for(None, tenant_id=str(uuid.uuid4()))

        response = client.post(f"/segments/{uuid.uuid4()}/recompute")

        self.assertEqual(response.status_code, 404)

    def test_recompute_400_when_no_sql_rules(self):
        segment = SimpleNamespace(segment_id=uuid.uuid4(), tenant_id=uuid.uuid4(), sql_rules=None)
        client = self._client_for(segment)

        response = client.post(f"/segments/{segment.segment_id}/recompute")

        self.assertEqual(response.status_code, 400)

    def test_recompute_400_when_membership_recompute_rejects_unsafe_rules(self):
        segment = SimpleNamespace(
            segment_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            sql_rules="1=1; DROP TABLE cdp_master_profiles;",
        )
        client = self._client_for(segment)

        # Mock the repository to raise ValueError when recompute_membership is called
        def mock_repo_factory_with_error(db):
            repo = SimpleNamespace()
            repo.get_segment = lambda seg_id: segment
            repo.recompute_membership = lambda seg_id: (_ for _ in ()).throw(
                ValueError("sql_rules must not contain statement separators")
            )
            return repo

        with patch(
            "core.routers.segment_api.SegmentRepository",
            side_effect=mock_repo_factory_with_error
        ):
            response = client.post(f"/segments/{segment.segment_id}/recompute")

        self.assertEqual(response.status_code, 400)

    @patch("core.routers.segment_api.dagster_client.segmentation.refresh", return_value="run-segment")
    def test_recompute_submits_tenant_and_segment_scoped_job(self, mock_trigger):
        segment_id = uuid.uuid4()
        last_computed_at = datetime(2026, 7, 28, 10, 15, tzinfo=timezone.utc)

        segment = SimpleNamespace(
            segment_id=segment_id,
            tenant_id=uuid.uuid4(),
            sql_rules="churn_risk_tier IN ('high', 'critical')",
            member_count=42,
            last_computed_at=last_computed_at,
        )
        client = self._client_for(segment)

        response = client.post(f"/segments/{segment_id}/recompute")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["segment_id"], str(segment_id))
        self.assertEqual(body["tenant_id"], str(segment.tenant_id))
        self.assertEqual(body["run_id"], "run-segment")
        mock_trigger.assert_called_once_with(
            tenant_id=str(segment.tenant_id),
            segment_id=str(segment_id),
        )


class SegmentAdminSeedDefaultsTests(unittest.TestCase):
    def setUp(self):
        import core.routers.segment_api as segment_router_module

        self.segment_router_module = segment_router_module
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

    def _client_with_state(
        self,
        *,
        tenant_id: Optional[str],
        user_payload: Optional[dict[str, Any]],
    ) -> TestClient:
        app = FastAPI()

        @app.middleware("http")
        async def _inject_state(request, call_next):
            if tenant_id is not None:
                request.state.tenant_id = tenant_id
            if user_payload is not None:
                request.state.user = user_payload
            return await call_next(request)

        app.include_router(self.segment_router_module.segments_router)
        app.dependency_overrides[get_db] = lambda: None
        return TestClient(app)

    def test_seed_defaults_rejects_tenant_and_all_tenants_together(self):
        client = self._client_with_state(tenant_id=str(uuid.uuid4()), user_payload={"realm_access": {"roles": ["admin"]}})

        response = client.post(f"/segments/admin/defaults/seed?tenant_id={uuid.uuid4()}&all_tenants=true")

        self.assertEqual(response.status_code, 400)

    def test_seed_defaults_for_all_tenants_requires_platform_admin(self):
        client = self._client_with_state(tenant_id=str(uuid.uuid4()), user_payload={"realm_access": {"roles": ["admin"]}})

        with patch("core.routers.segment_api.settings.sso_login", True):
            response = client.post("/segments/admin/defaults/seed?all_tenants=true")

        self.assertEqual(response.status_code, 403)

    def test_seed_defaults_for_caller_tenant_requires_tenant_admin(self):
        client = self._client_with_state(tenant_id=str(uuid.uuid4()), user_payload={"realm_access": {"roles": ["analyst"]}})

        with patch("core.routers.segment_api.settings.sso_login", True):
            response = client.post("/segments/admin/defaults/seed")

        self.assertEqual(response.status_code, 403)

    def test_seed_defaults_for_current_tenant_returns_insert_breakdown(self):
        caller_tenant_id = uuid.uuid4()
        client = self._client_with_state(
            tenant_id=str(caller_tenant_id),
            user_payload={"realm_access": {"roles": ["tenant_admin"]}},
        )

        with patch("core.routers.segment_api.settings.sso_login", True), patch(
            "core.routers.segment_api.seed_default_segments_with_breakdown",
            return_value=(3, {caller_tenant_id: 3}),
        ) as mock_seed:
            response = client.post("/segments/admin/defaults/seed")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["requested_all_tenants"], False)
        self.assertEqual(body["seeded_tenants"], 1)
        self.assertEqual(body["inserted_segments"], 3)
        self.assertEqual(body["results"], [{"tenant_id": str(caller_tenant_id), "inserted": 3}])
        mock_seed.assert_called_once()

    def test_seed_defaults_for_all_tenants_as_platform_admin(self):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        client = self._client_with_state(
            tenant_id=str(uuid.uuid4()),
            user_payload={"realm_access": {"roles": ["platform_admin"]}},
        )

        with patch("core.routers.segment_api.settings.sso_login", True), patch(
            "core.routers.segment_api.list_tenant_ids",
            return_value=[tenant_a, tenant_b],
        ), patch(
            "core.routers.segment_api.seed_default_segments_with_breakdown",
            return_value=(5, {tenant_a: 2, tenant_b: 3}),
        ):
            response = client.post("/segments/admin/defaults/seed?all_tenants=true")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["requested_all_tenants"], True)
        self.assertEqual(body["seeded_tenants"], 2)
        self.assertEqual(body["inserted_segments"], 5)
        self.assertEqual(
            body["results"],
            [
                {"tenant_id": str(tenant_a), "inserted": 2},
                {"tenant_id": str(tenant_b), "inserted": 3},
            ],
        )


class SegmentAdminRecomputeAllTests(unittest.TestCase):
    def setUp(self):
        import core.routers.segment_api as segment_router_module

        self.segment_router_module = segment_router_module
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

    def _client_with_state(
        self,
        *,
        tenant_id: Optional[str],
        user_payload: Optional[dict[str, Any]],
    ) -> TestClient:
        app = FastAPI()

        @app.middleware("http")
        async def _inject_state(request, call_next):
            if tenant_id is not None:
                request.state.tenant_id = tenant_id
            if user_payload is not None:
                request.state.user = user_payload
            return await call_next(request)

        app.include_router(self.segment_router_module.segments_router)
        app.dependency_overrides[get_db] = lambda: None
        return TestClient(app)

    def test_recompute_all_rejects_missing_tenant_context(self):
        client = self._client_with_state(tenant_id=None, user_payload=None)

        response = client.post("/segments/admin/recompute-all")

        self.assertEqual(response.status_code, 400)

    def test_recompute_all_requires_tenant_admin(self):
        client = self._client_with_state(tenant_id=str(uuid.uuid4()), user_payload={"realm_access": {"roles": ["analyst"]}})

        with patch("core.routers.segment_api.settings.sso_login", True):
            response = client.post("/segments/admin/recompute-all")

        self.assertEqual(response.status_code, 403)

    def test_recompute_all_triggers_job_scoped_to_caller_tenant_only(self):
        caller_tenant_id = uuid.uuid4()
        client = self._client_with_state(
            tenant_id=str(caller_tenant_id),
            user_payload={"realm_access": {"roles": ["tenant_admin"]}},
        )

        with patch("core.routers.segment_api.settings.sso_login", True), patch(
            "core.routers.segment_api.dagster_client.segmentation.refresh",
            return_value="run-123",
        ) as mock_trigger:
            response = client.post("/segments/admin/recompute-all")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["run_id"], "run-123")
        self.assertEqual(body["tenant_id"], str(caller_tenant_id))
        mock_trigger.assert_called_once_with(tenant_id=str(caller_tenant_id))

    def test_recompute_all_surfaces_dagster_trigger_error_as_503(self):
        client = self._client_with_state(
            tenant_id=str(uuid.uuid4()),
            user_payload={"realm_access": {"roles": ["tenant_admin"]}},
        )

        with patch("core.routers.segment_api.settings.sso_login", True), patch(
            "core.routers.segment_api.dagster_client.segmentation.refresh",
            side_effect=self.segment_router_module.DagsterJobTriggerError("boom"),
        ):
            response = client.post("/segments/admin/recompute-all")

        self.assertEqual(response.status_code, 503)


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


class _FakeMultiSelectSession:
    """Session double for endpoints issuing several sequential db.execute()
    calls (e.g. a sys_domain validation lookup, then a data query) --
    returns one canned `.scalars().all()` result per call, in order."""

    def __init__(self, row_sets: list[list[Any]]):
        self._results = [_FakeScalarsResult(rows) for rows in row_sets]
        self.executed: list[Any] = []

    def execute(self, stmt: Any, params: Optional[dict[str, Any]] = None) -> Any:
        self.executed.append(stmt)
        return self._results.pop(0)


def _fake_profile_attribute(**overrides) -> SimpleNamespace:
    attrs = {
        "master_profile_column": "churn_risk_tier",
        "attribute_internal_code": "churn_risk_tier",
        "name": "Churn Risk Tier",
        "description": "Predicted churn risk bucket.",
        "attribute_group": "CHURN_SCORING",
        "data_type": "TEXT",
        "domain_scope": "all",
        "is_pii": False,
        "source_table": "cdp_master_profiles",
    }
    attrs.update(overrides)
    return SimpleNamespace(**attrs)


class SegmentableProfileAttributesTests(unittest.TestCase):
    """Tests GET /segments/segmentable-profile-attributes against the REAL
    segments_router -- in particular that it isn't shadowed by the generic
    CRUD router's GET /{item_id} (registered earlier, on the same path
    shape), which would otherwise swallow this literal route and return a
    422 "invalid UUID" instead of ever running the handler."""

    def setUp(self):
        import core.routers.segment_api as segment_router_module

        self.segment_router_module = segment_router_module
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        self.app = FastAPI()
        self.app.include_router(segment_router_module.segments_router)

    def _client_for(self, rows: list[Any]) -> tuple[TestClient, _FakeSelectSession]:
        session = _FakeSelectSession(rows)
        self.app.dependency_overrides[get_db] = lambda: session
        return TestClient(self.app), session

    def test_route_is_not_shadowed_by_generic_get_by_id(self):
        client, _ = self._client_for([])

        response = client.get("/segments/segmentable-profile-attributes")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_returns_expected_shape_preferring_master_profile_column(self):
        attribute = _fake_profile_attribute()
        client, _ = self._client_for([attribute])

        response = client.get("/segments/segmentable-profile-attributes")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "field": "churn_risk_tier",
                    "name": "Churn Risk Tier",
                    "description": "Predicted churn risk bucket.",
                    "attribute_group": "CHURN_SCORING",
                    "data_type": "TEXT",
                    "domain_scope": "all",
                    "is_pii": False,
                }
            ],
        )

    def test_falls_back_to_attribute_internal_code_when_no_master_column(self):
        attribute = _fake_profile_attribute(master_profile_column=None, attribute_internal_code="device_id")
        client, _ = self._client_for([attribute])

        response = client.get("/segments/segmentable-profile-attributes")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["field"], "device_id")

    def test_domain_profile_attribute_returns_jsonb_path_field(self):
        attribute = _fake_profile_attribute(
            master_profile_column=None,
            attribute_internal_code="risk_segment",
            source_table="cdp_domain_profiles",
        )
        client, _ = self._client_for([attribute])

        response = client.get("/segments/segmentable-profile-attributes")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["field"], "dp.domain_attributes->>'risk_segment'")

    def test_accepts_valid_domain_query_param(self):
        # Two sequential db.execute() calls now: validate_domain_value's
        # sys_domain lookup, then the main attributes select.
        session = _FakeMultiSelectSession([["retail"], []])
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.get("/segments/segmentable-profile-attributes", params={"domain": "retail"})

        self.assertEqual(response.status_code, 200)

    def test_rejects_invalid_domain_query_param(self):
        session = _FakeMultiSelectSession([["retail"], []])
        self.app.dependency_overrides[get_db] = lambda: session
        client = TestClient(self.app)

        response = client.get("/segments/segmentable-profile-attributes", params={"domain": "bogus"})

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
