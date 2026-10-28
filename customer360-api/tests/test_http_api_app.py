"""Contract tests for the Customer 360 HTTP application factory."""

import asyncio

import pytest
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.apps.http_api_app as http_api_app
from core.apps.http_api_app import create_http_api_app


def test_factory_registers_independent_metadata_resource_prefixes():
    paths = create_http_api_app(FastAPI()).openapi()["paths"]

    assert "/api/v1/metadata/" in paths
    assert "/api/v1/data-sources/" in paths
    assert "/api/v1/ai-agents/" in paths
    assert not any(path.startswith("/api/v1/metadata/data-sources") for path in paths)
    assert not any(path.startswith("/api/v1/metadata/ai-agents") for path in paths)


def test_openapi_marks_only_public_paths_as_unauthenticated():
    schema = create_http_api_app(FastAPI()).openapi()

    assert "security" not in schema["paths"]["/api/v1/metadata/"]["get"]
    assert schema["paths"]["/api/v1/data-sources/"]["get"]["security"] == [{"BearerAuth": []}]
    assert schema["paths"]["/api/v1/ai-agents/"]["post"]["security"] == [{"BearerAuth": []}]


def test_health_reports_git_commit_hash(monkeypatch):
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def execute(self, statement):
            assert str(statement) == "SELECT 1"

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    monkeypatch.setattr(http_api_app, "engine", FakeEngine())
    monkeypatch.setattr(http_api_app, "GIT_COMMIT_HASH", "test-commit")

    response = TestClient(create_http_api_app(FastAPI())).get("/health")

    assert response.status_code == 200
    assert response.json()["GIT_COMMIT_HASH"] == "test-commit"


def test_lifespan_ensures_import_buckets_before_database_initialization(monkeypatch):
    calls = []
    monkeypatch.setattr(
        http_api_app,
        "ensure_import_buckets",
        lambda: calls.append("buckets") or ("product-bucket", "content-bucket"),
    )
    monkeypatch.setattr(http_api_app, "init_core_data", lambda: calls.append("core"))

    async def run_lifespan():
        async with http_api_app._lifespan(FastAPI()):
            assert calls == ["buckets", "core"]

    asyncio.run(run_lifespan())


def test_lifespan_fails_before_initializing_core_data_if_bucket_setup_fails(monkeypatch):
    initialized = False

    def fail_bucket_setup():
        raise RuntimeError("S3 unavailable")

    def init_core_data():
        nonlocal initialized
        initialized = True

    monkeypatch.setattr(http_api_app, "ensure_import_buckets", fail_bucket_setup)
    monkeypatch.setattr(http_api_app, "init_core_data", init_core_data)

    async def run_lifespan():
        async with http_api_app._lifespan(FastAPI()):
            pass

    with pytest.raises(RuntimeError, match="S3 unavailable"):
        asyncio.run(run_lifespan())
    assert not initialized


def test_startup_ensures_import_buckets_before_core_initialization(monkeypatch):
    order = []
    monkeypatch.setattr(
        http_api_app,
        "ensure_import_buckets",
        lambda: order.append("buckets") or ("products", "content"),
    )
    monkeypatch.setattr(http_api_app, "init_core_data", lambda: order.append("core"))
    app = FastAPI()

    async def run_lifespan():
        async with http_api_app._lifespan(app):
            assert order == ["buckets", "core"]

    asyncio.run(run_lifespan())


def test_startup_does_not_continue_if_import_buckets_cannot_be_ready(monkeypatch):
    initialized = False

    def fail_bucket_check():
        raise RuntimeError("S3 unavailable")

    def init_core():
        nonlocal initialized
        initialized = True

    monkeypatch.setattr(http_api_app, "ensure_import_buckets", fail_bucket_check)
    monkeypatch.setattr(http_api_app, "init_core_data", init_core)

    async def run_lifespan():
        async with http_api_app._lifespan(FastAPI()):
            pass

    with pytest.raises(RuntimeError, match="S3 unavailable"):
        asyncio.run(run_lifespan())
    assert not initialized