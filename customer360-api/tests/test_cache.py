"""Tests for tenant-aware API response caching."""

import uuid

from fastapi import Request

import core.cache as cache


def _request(tenant_id: str) -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/"})
    request.state.tenant_id = tenant_id
    return request


def test_cache_response_isolated_by_request_tenant(monkeypatch):
    stored = {}
    calls = []

    monkeypatch.setattr(cache, "cache_get", lambda key: stored.get(key))
    monkeypatch.setattr(
        cache,
        "cache_set",
        lambda key, value, _ttl=None: stored.__setitem__(key, value),
    )

    @cache.cache_response("tests/tenant-isolation")
    def load_profile(request: Request, profile_id: uuid.UUID, limit: int = 8):
        calls.append(request.state.tenant_id)
        return {"tenant_id": request.state.tenant_id, "profile_id": str(profile_id), "limit": limit}

    profile_id = uuid.uuid4()
    tenant_a = _request("tenant-a")
    tenant_b = _request("tenant-b")

    assert load_profile(tenant_a, profile_id, 8)["tenant_id"] == "tenant-a"
    assert load_profile(tenant_a, profile_id, 8)["tenant_id"] == "tenant-a"
    assert load_profile(tenant_b, profile_id, 8)["tenant_id"] == "tenant-b"

    assert calls == ["tenant-a", "tenant-b"]
    assert len(stored) == 2


def test_cache_response_separates_timeline_source_filters(monkeypatch):
    stored = {}
    keys = []

    def fake_get(key):
        keys.append(key)
        return stored.get(key)

    monkeypatch.setattr(cache, "cache_get", fake_get)
    monkeypatch.setattr(
        cache,
        "cache_set",
        lambda key, value, _ttl=None: stored.__setitem__(key, value),
    )

    @cache.cache_response("tests/timeline")
    def load_timeline(
        request: Request,
        profile_id: uuid.UUID,
        limit: int = 8,
        data_source_id: uuid.UUID | None = None,
    ):
        return {"profile_id": str(profile_id), "limit": limit, "data_source_id": str(data_source_id)}

    request = _request("tenant-a")
    profile_id = uuid.uuid4()
    source_a = uuid.uuid4()
    source_b = uuid.uuid4()

    load_timeline(request, profile_id, 8, source_a)
    load_timeline(request, profile_id, 8, source_b)

    assert len(keys) == 2
    assert keys[0] != keys[1]
