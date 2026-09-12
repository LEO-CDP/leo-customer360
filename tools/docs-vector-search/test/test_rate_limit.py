from __future__ import annotations

import asyncio

from starlette.requests import Request

from src.rate_limit import RedisRequestLimiter


def _request(headers: list[tuple[bytes, bytes]], client: tuple[str, int] = ("10.0.0.8", 1234)) -> Request:
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


def test_limiter_checks_ip_and_browser_buckets(monkeypatch):
    asyncio.run(_check_ip_and_browser_buckets(monkeypatch))


async def _check_ip_and_browser_buckets(monkeypatch):
    limiter = RedisRequestLimiter()
    calls = []

    class FakeRedis:
        async def eval(self, *args):
            calls.append(args)
            return [1, 0]

    limiter.client = FakeRedis()
    monkeypatch.setattr("src.rate_limit.ASK_RATE_MAX", 10)
    monkeypatch.setattr(limiter, "enabled", True)

    await limiter.enforce(
        _request(
            [
                (b"user-agent", b"Browser/1"),
                (b"accept-language", b"vi-VN"),
                (b"x-forwarded-for", b"203.0.113.9"),
            ]
        )
    )

    assert len(calls) == 1
    assert calls[0][1] == 2
    assert calls[0][2].startswith("docs:ratelimit:ip:")
    assert calls[0][3].startswith("docs:ratelimit:browser:")


def test_limiter_rejects_when_redis_reports_exhausted(monkeypatch):
    asyncio.run(_check_exhausted_bucket(monkeypatch))


async def _check_exhausted_bucket(monkeypatch):
    limiter = RedisRequestLimiter()

    class FakeRedis:
        async def eval(self, *args):
            return [0, 7]

    limiter.client = FakeRedis()
    monkeypatch.setattr(limiter, "enabled", True)

    try:
        await limiter.enforce(_request([(b"user-agent", b"Browser/1")]))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 429
        assert exc.headers["Retry-After"] == "7"
    else:
        raise AssertionError("rate limiter did not reject an exhausted bucket")