from __future__ import annotations

from fastapi import HTTPException
from starlette.requests import Request

from src import server


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/reindex",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("docs", 8001),
        }
    )


def test_reindex_access_requires_the_internal_secret(monkeypatch):
    monkeypatch.setattr(server, "INTERNAL_API_SECRET", "test-secret")
    monkeypatch.setattr(server.limiter, "is_internal", lambda request: False)

    try:
        server._require_reindex_access(_request([]))
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("unauthenticated reindex access was accepted")


def test_reindex_access_accepts_a_trusted_internal_request(monkeypatch):
    monkeypatch.setattr(server, "INTERNAL_API_SECRET", "test-secret")
    monkeypatch.setattr(server.limiter, "is_internal", lambda request: True)

    server._require_reindex_access(_request([(b"x-internal-auth", b"test-secret")]))