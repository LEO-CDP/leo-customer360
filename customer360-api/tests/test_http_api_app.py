"""Contract tests for the Customer 360 HTTP application factory."""

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