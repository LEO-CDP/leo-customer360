from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src import config, rate_limit
from src.rate_limit import RedisRequestLimiter


def _request(
    headers: list[tuple[bytes, bytes]],
    client: tuple[str, int] = ("10.0.0.8", 1234),
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/ask",
            "headers": headers,
            "client": client,
            "server": ("docs", 8001),
        }
    )


class FakePipeline:
    def __init__(self, result: list[object]):
        self.result = result
        self.operations: list[tuple[str, object]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def incr(self, key: str):
        self.operations.append(("incr", key))

    def expire(self, key: str, seconds: int):
        self.operations.append(("expire", (key, seconds)))

    async def execute(self):
        return self.result


class FakeRedis:
    def __init__(self, result: list[object] | None = None, error: Exception | None = None):
        self.pipeline_instance = FakePipeline(result or [1, True])
        self.error = error

    def pipeline(self, transaction: bool):
        assert transaction is True
        if self.error:
            return FailingPipeline(self.error)
        return self.pipeline_instance


class FailingPipeline:
    def __init__(self, error: Exception):
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def incr(self, key: str):
        pass

    def expire(self, key: str, seconds: int):
        pass

    async def execute(self):
        raise self.error


def test_allowed_hosts_are_trimmed_and_normalized():
    assert config._parse_allowed_hosts(" localhost, Admin.Example.com., , ") == {
        "localhost",
        "admin.example.com",
    }


def test_limiter_uses_atomic_pipeline_for_allowed_host(monkeypatch):
    async def check():
        limiter = RedisRequestLimiter()
        fake_redis = FakeRedis()
        setattr(limiter, "client", fake_redis)
        monkeypatch.setattr(rate_limit, "LEO_BOT_ALLOWED_HOSTS", {"docs.example.com"})
        monkeypatch.setattr(rate_limit, "ASK_RATE_MAX", 10)
        monkeypatch.setattr(rate_limit, "ASK_RATE_WINDOW_SEC", 60)
        monkeypatch.setattr(rate_limit.time, "time", lambda: 121)
        limiter.enabled = True

        await limiter.enforce(
            _request(
                [
                    (b"host", b"DOCS.EXAMPLE.COM:8001"),
                    (b"user-agent", b"Browser/1"),
                    (b"accept-language", b"vi-VN"),
                    (b"x-forwarded-for", b"203.0.113.9"),
                ]
            )
        )

        operations = fake_redis.pipeline_instance.operations
        assert operations[0][0] == "incr"
        key = operations[0][1]
        assert isinstance(key, str)
        assert key.startswith("docs:ratelimit:client:")
        assert key.endswith(":2")
        assert operations[1] == ("expire", (operations[0][1], 120))

    asyncio.run(check())


def test_limiter_rejects_unrecognized_host_before_redis(monkeypatch):
    async def check():
        limiter = RedisRequestLimiter()
        fake_redis = FakeRedis()
        setattr(limiter, "client", fake_redis)
        monkeypatch.setattr(rate_limit, "LEO_BOT_ALLOWED_HOSTS", {"docs.example.com"})
        limiter.enabled = True

        with pytest.raises(HTTPException) as exc_info:
            await limiter.enforce(_request([(b"host", b"attacker.example.com")]))

        assert exc_info.value.status_code == 403
        assert fake_redis.pipeline_instance.operations == []

    asyncio.run(check())


def test_internal_proxy_bypasses_private_host_and_rate_limit(monkeypatch):
    async def check():
        limiter = RedisRequestLimiter()
        fake_redis = FakeRedis()
        setattr(limiter, "client", fake_redis)
        monkeypatch.setattr(rate_limit, "LEO_BOT_ALLOWED_HOSTS", {"docs.example.com"})
        monkeypatch.setattr(rate_limit, "INTERNAL_API_SECRET", "proxy-secret")
        limiter.enabled = True

        await limiter.enforce(
            _request(
                [
                    (b"host", b"docs-vector-search:8001"),
                    (b"x-internal-auth", b"proxy-secret"),
                ]
            )
        )

        assert fake_redis.pipeline_instance.operations == []

    asyncio.run(check())


def test_limiter_returns_429_after_limit(monkeypatch):
    async def check():
        limiter = RedisRequestLimiter()
        setattr(limiter, "client", FakeRedis(result=[11, True]))
        monkeypatch.setattr(rate_limit, "LEO_BOT_ALLOWED_HOSTS", {"docs.example.com"})
        monkeypatch.setattr(rate_limit, "ASK_RATE_MAX", 10)
        monkeypatch.setattr(rate_limit, "ASK_RATE_WINDOW_SEC", 60)
        monkeypatch.setattr(rate_limit.time, "time", lambda: 121)
        limiter.enabled = True

        with pytest.raises(HTTPException) as exc_info:
            await limiter.enforce(_request([(b"host", b"docs.example.com")]))

        assert exc_info.value.status_code == 429
        assert exc_info.value.headers is not None
        assert exc_info.value.headers["Retry-After"] == "59"

    asyncio.run(check())


def test_limiter_maps_redis_failure_to_503(monkeypatch):
    async def check():
        limiter = RedisRequestLimiter()
        setattr(
            limiter,
            "client",
            FakeRedis(error=rate_limit.redis.RedisError("offline")),
        )
        monkeypatch.setattr(rate_limit, "LEO_BOT_ALLOWED_HOSTS", {"docs.example.com"})
        limiter.enabled = True

        with pytest.raises(HTTPException) as exc_info:
            await limiter.enforce(_request([(b"host", b"docs.example.com")]))

        assert exc_info.value.status_code == 503
        assert exc_info.value.headers is not None
        assert exc_info.value.headers["Retry-After"] == "5"

    asyncio.run(check())