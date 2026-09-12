"""Redis-backed request limiting for docs endpoints."""
from __future__ import annotations

import hashlib
import hmac
import uuid

import redis.asyncio as redis
from fastapi import HTTPException, Request

from .config import (
    ASK_RATE_MAX,
    ASK_RATE_WINDOW_SEC,
    DOCS_REDIS_CONNECT_TIMEOUT_SECONDS,
    DOCS_REDIS_DB,
    DOCS_REDIS_HOST,
    DOCS_REDIS_PASSWORD,
    DOCS_REDIS_PORT,
    DOCS_REDIS_SOCKET_TIMEOUT_SECONDS,
    INTERNAL_API_SECRET,
    TRUSTED_PROXY_HOPS,
)

_LIMIT_SCRIPT = """
local now = redis.call('TIME')
local now_ms = (now[1] * 1000) + math.floor(now[2] / 1000)
local window_ms = tonumber(ARGV[1]) * 1000
local max_hits = tonumber(ARGV[2])
local member = ARGV[3]
for _, key in ipairs(KEYS) do
  redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - window_ms)
  if redis.call('ZCARD', key) >= max_hits then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_ms = window_ms - (now_ms - tonumber(oldest[2]))
    return {0, math.max(1, math.ceil(retry_ms / 1000))}
  end
end
for _, key in ipairs(KEYS) do
  redis.call('ZADD', key, now_ms, member)
  redis.call('EXPIRE', key, tonumber(ARGV[1]) + 1)
end
return {1, 0}
"""


class RedisRequestLimiter:
    def __init__(self) -> None:
        self.enabled = ASK_RATE_MAX > 0
        self.client = redis.Redis(
            host=DOCS_REDIS_HOST,
            port=DOCS_REDIS_PORT,
            db=DOCS_REDIS_DB,
            password=DOCS_REDIS_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=DOCS_REDIS_CONNECT_TIMEOUT_SECONDS,
            socket_timeout=DOCS_REDIS_SOCKET_TIMEOUT_SECONDS,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def ping(self) -> None:
        if self.enabled:
            await self.client.ping()

    async def enforce(self, request: Request) -> None:
        if not self.enabled or self._is_internal(request):
            return
        keys = [
            f"docs:ratelimit:ip:{self._digest(self._client_ip(request))}",
            f"docs:ratelimit:browser:{self._digest(self._browser_fingerprint(request))}",
        ]
        try:
            result = await self.client.eval(
                _LIMIT_SCRIPT,
                len(keys),
                *keys,
                ASK_RATE_WINDOW_SEC,
                ASK_RATE_MAX,
                uuid.uuid4().hex,
            )
        except redis.RedisError as exc:
            raise HTTPException(
                status_code=503,
                detail="Request limiter is temporarily unavailable; please retry.",
                headers={"Retry-After": "5"},
            ) from exc
        if int(result[0]) == 0:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded — please wait a moment and try again.",
                headers={"Retry-After": str(int(result[1]))},
            )

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:32]

    @staticmethod
    def _is_internal(request: Request) -> bool:
        supplied = request.headers.get("x-internal-auth", "")
        return bool(INTERNAL_API_SECRET and hmac.compare_digest(supplied, INTERNAL_API_SECRET))

    @staticmethod
    def _client_ip(request: Request) -> str:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            entries = [entry.strip() for entry in xff.split(",") if entry.strip()]
            if len(entries) >= TRUSTED_PROXY_HOPS:
                return entries[-TRUSTED_PROXY_HOPS]
        return request.client.host if request.client else "unknown"

    @staticmethod
    def _browser_fingerprint(request: Request) -> str:
        return "|".join(
            request.headers.get(name, "")
            for name in ("user-agent", "accept-language", "sec-ch-ua", "sec-ch-ua-platform")
        )


limiter = RedisRequestLimiter()