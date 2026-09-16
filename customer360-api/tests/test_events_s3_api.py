"""HTTP contract tests for the S3-backed `/events/` compatibility route."""

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.cache as cache_module
from core.database import get_db
from core.routers.events_s3_api import get_event_query_repository, router


TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class FakeDb:
    pass


class FakeRepository:
    def __init__(self):
        self.calls = []

    def query(self, db, tenant_id, **kwargs):
        self.calls.append((db, tenant_id, kwargs))
        return [
            {
                "event_id": "external-event-1",
                "event_time": "2026-09-15T14:00:00+00:00",
                "tenant_id": str(tenant_id),
                "domain": "retail",
                "event_category": "COMMERCE",
                "event_name": "purchase",
                "event_payload": {"order_id": "order-1"},
            }
        ]


def test_events_route_queries_s3_with_authenticated_tenant_and_bounds(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    fake_repository = FakeRepository()
    monkeypatch.setattr(cache_module, "get_redis_client", lambda: None)
    app.dependency_overrides[get_db] = lambda: FakeDb()
    app.dependency_overrides[get_event_query_repository] = lambda: fake_repository

    @app.middleware("http")
    async def tenant_middleware(request, call_next):
        request.state.tenant_id = str(TENANT_ID)
        return await call_next(request)
    try:
        response = TestClient(app).get(
            "/events/",
            params={
                "event_time_from": "2026-06-17T17:49:58.434Z",
                "limit": 1000,
                "days": 90,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["event_name"] == "purchase"
    assert fake_repository.calls[0][1] == TENANT_ID
    assert fake_repository.calls[0][2]["limit"] == 1000
    assert fake_repository.calls[0][2]["days"] == 90


def test_events_route_rejects_days_above_configured_bound():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: FakeDb()

    @app.middleware("http")
    async def tenant_middleware(request, call_next):
        request.state.tenant_id = str(TENANT_ID)
        return await call_next(request)
    try:
        response = TestClient(app).get("/events/?days=181")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_events_route_returns_not_found_for_invalid_or_inactive_source():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: FakeDb()

    @app.middleware("http")
    async def tenant_middleware(request, call_next):
        request.state.tenant_id = str(TENANT_ID)
        return await call_next(request)

    class InvalidSourceRepository(FakeRepository):
        def query(self, db, tenant_id, **kwargs):
            from core.repositories.event_query_repository import EventDataSourceError

            raise EventDataSourceError(
                "Data source is invalid, inactive, or not owned by the tenant"
            )

    invalid_repository = InvalidSourceRepository()
    app.dependency_overrides[get_event_query_repository] = lambda: invalid_repository
    try:
        response = TestClient(app).get(
            "/events/",
            params={"data_source_id": str(uuid.uuid4())},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert "invalid, inactive" in response.json()["detail"]


def test_events_route_caches_per_tenant_and_event_time_filter(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    fake_repository = FakeRepository()
    app.dependency_overrides[get_db] = lambda: FakeDb()
    app.dependency_overrides[get_event_query_repository] = lambda: fake_repository
    fake_redis = {}

    class InMemoryRedis:
        def get(self, key):
            return fake_redis.get(key)

        def set(self, key, value, ex=None):
            fake_redis[key] = value

    monkeypatch.setattr(cache_module, "get_redis_client", lambda: InMemoryRedis())

    @app.middleware("http")
    async def tenant_middleware(request, call_next):
        request.state.tenant_id = request.headers["X-Tenant-Id"]
        return await call_next(request)

    first_filter = "2026-09-15T00:00:00Z"
    second_filter = "2026-09-16T00:00:00Z"
    try:
        client = TestClient(app)
        first = client.get(
            "/events/",
            headers={"X-Tenant-Id": str(TENANT_ID)},
            params={"event_time_from": first_filter},
        )
        cached = client.get(
            "/events/",
            headers={"X-Tenant-Id": str(TENANT_ID)},
            params={"event_time_from": first_filter},
        )
        different_filter = client.get(
            "/events/",
            headers={"X-Tenant-Id": str(TENANT_ID)},
            params={"event_time_from": second_filter},
        )
        different_tenant = client.get(
            "/events/",
            headers={"X-Tenant-Id": str(uuid.UUID("33333333-3333-3333-3333-333333333333"))},
            params={"event_time_from": first_filter},
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert cached.status_code == 200
    assert different_filter.status_code == 200
    assert different_tenant.status_code == 200
    assert len(fake_repository.calls) == 3
