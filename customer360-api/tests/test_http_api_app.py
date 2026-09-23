"""Contract tests for the Customer 360 HTTP application factory."""

from fastapi import FastAPI

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