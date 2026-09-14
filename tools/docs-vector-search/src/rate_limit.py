"""Redis-backed request limiting for docs endpoints."""
from __future__ import annotations

import hashlib
import hmac
import time

import redis.asyncio as redis
from fastapi import HTTPException, Request

from .config import (
    LEO_BOT_ALLOWED_HOSTS,
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
        """Close the async Redis client connection."""
        await self.client.aclose()

    async def ping(self) -> None:
        """Health check for the Redis connection."""
        if self.enabled:
            await self.client.ping()

    async def enforce(self, request: Request) -> None:
        """Validate public hosts and enforce a fixed-window client limit."""
        # The admin proxy is authenticated separately and may use a private service
        # hostname that is intentionally absent from the public host allow-list.
        if self.is_internal(request):
            return

        host = self._request_host(request)
        if host not in LEO_BOT_ALLOWED_HOSTS:
            raise HTTPException(
                status_code=403,
                detail="Host origin is not whitelisted for this endpoint.",
            )

        if not self.enabled:
            return

        # 3. Gather client identification factors
        client_ip = self._client_ip(request)
        browser_fp = self._browser_fingerprint(request)

        # 4. Union all factors into a single unique identity string and hash it
        combined_identity = f"{host}|{client_ip}|{browser_fp}"
        identity_hash = self._digest(combined_identity)

        # 5. Calculate Fixed Window ID
        now = int(time.time())
        current_window = now // ASK_RATE_WINDOW_SEC

        # Single Redis key representing this specific client for this specific time window
        limit_key = f"docs:ratelimit:client:{identity_hash}:{current_window}"

        try:
            # Execute INCR and EXPIRE in a single atomic pipeline
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.incr(limit_key)
                pipe.expire(limit_key, ASK_RATE_WINDOW_SEC * 2)
                results = await pipe.execute()
        except redis.RedisError as exc:
            raise HTTPException(
                status_code=503,
                detail="Request limiter is temporarily unavailable; please retry.",
                headers={"Retry-After": "5"},
            ) from exc

        request_count = results[0]

        # 6. Reject if the single combined threshold is exceeded
        if request_count > ASK_RATE_MAX:
            retry_after = ASK_RATE_WINDOW_SEC - (now % ASK_RATE_WINDOW_SEC)
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded — please wait a moment and try again.",
                headers={"Retry-After": str(max(1, retry_after))},
            )

    @staticmethod
    def _digest(value: str) -> str:
        """Creates a stable sha256 digest string to keep Redis memory usage low and uniform."""
        return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:32]

    @staticmethod
    def _request_host(request: Request) -> str:
        """Return the normalized hostname without a port or trailing dot."""
        return (request.url.hostname or "").lower().rstrip(".")

    @staticmethod
    def is_internal(request: Request) -> bool:
        """Checks if request bypasses rate limits using an internal auth secret."""
        supplied = request.headers.get("x-internal-auth", "")
        return bool(INTERNAL_API_SECRET and hmac.compare_digest(supplied, INTERNAL_API_SECRET))

    @staticmethod
    def _client_ip(request: Request) -> str:
        """Determines true client IP through trusted proxy hops."""
        xff = request.headers.get("x-forwarded-for")
        if xff:
            entries = [entry.strip() for entry in xff.split(",") if entry.strip()]
            if len(entries) >= TRUSTED_PROXY_HOPS:
                return entries[-TRUSTED_PROXY_HOPS]
        return request.client.host if request.client else "unknown"

    @staticmethod
    def _browser_fingerprint(request: Request) -> str:
        """Constructs a fingerprint string based on browser headers."""
        return "|".join(
            request.headers.get(name, "")
            for name in ("user-agent", "accept-language", "sec-ch-ua", "sec-ch-ua-platform")
        )

limiter = RedisRequestLimiter()