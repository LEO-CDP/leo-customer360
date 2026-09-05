import asyncio
import pytest
from fastapi import HTTPException

from core import auth as auth_module


class _FakeRedis:
    def __init__(self, values=None, raise_on_get=False):
        self.values = values or {}
        self.raise_on_get = raise_on_get

    def get(self, key: str):
        if self.raise_on_get:
            raise RuntimeError("redis down")
        return self.values.get(key)


def test_resolve_mcp_tenant_id_reads_string_value(monkeypatch):
    redis = _FakeRedis({"apikey:key-1": "tenant-abc"})
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: redis)

    tenant_id = auth_module.resolve_mcp_tenant_id("key-1")

    assert tenant_id == "tenant-abc"


def test_resolve_mcp_tenant_id_reads_bytes_value(monkeypatch):
    redis = _FakeRedis({"apikey:key-1": b" tenant-bytes "})
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: redis)

    tenant_id = auth_module.resolve_mcp_tenant_id("key-1")

    assert tenant_id == "tenant-bytes"


def test_resolve_mcp_tenant_id_missing_key_raises_401(monkeypatch):
    redis = _FakeRedis({})
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: redis)

    with pytest.raises(HTTPException) as exc_info:
        auth_module.resolve_mcp_tenant_id("missing")

    assert exc_info.value.status_code == 401


def test_resolve_mcp_tenant_id_empty_mapping_raises_401(monkeypatch):
    redis = _FakeRedis({"apikey:key-1": "   "})
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: redis)

    with pytest.raises(HTTPException) as exc_info:
        auth_module.resolve_mcp_tenant_id("key-1")

    assert exc_info.value.status_code == 401


def test_resolve_mcp_tenant_id_no_redis_raises_503(monkeypatch):
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: None)

    with pytest.raises(HTTPException) as exc_info:
        auth_module.resolve_mcp_tenant_id("key-1")

    assert exc_info.value.status_code == 503


def test_resolve_mcp_tenant_id_redis_error_raises_500(monkeypatch):
    redis = _FakeRedis(raise_on_get=True)
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: redis)

    with pytest.raises(HTTPException) as exc_info:
        auth_module.resolve_mcp_tenant_id("key-1")

    assert exc_info.value.status_code == 500


def test_verify_mcp_api_key_returns_tenant_id(monkeypatch):
    monkeypatch.setattr(auth_module, "resolve_mcp_tenant_id", lambda api_key: "tenant-xyz")

    tenant_id = asyncio.run(auth_module.verify_mcp_api_key("key-1"))

    assert tenant_id == "tenant-xyz"
