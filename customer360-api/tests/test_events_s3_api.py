"""HTTP contract tests for the S3-backed `/events/` compatibility route."""

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def test_events_route_queries_s3_with_authenticated_tenant_and_bounds():
    app = FastAPI()
    app.include_router(router)
    fake_repository = FakeRepository()
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
        response = TestClient(app).get("/events/?days=91")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
