"""Redis-backed session metadata and request protection for tracking ingestion."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

import redis
from fastapi import Request

from core.config import Settings

logger = logging.getLogger(__name__)

_RATE_LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class RedisSessionCache:
    """Caches non-PII session activity metadata with an expiry."""

    def __init__(self, client: Any, key_prefix: str, ttl_seconds: int):
        self.client = client
        self.key_prefix = key_prefix
        self.ttl_seconds = ttl_seconds

    def touch_sessions(
        self,
        data_source_id: UUID,
        sessions: dict[str, tuple[int, Optional[str]]],
        seen_at: datetime,
    ) -> int:
        """Touch session keys and return the number of sessions updated.

        ``sessions`` maps a session ID to ``(event_count, user_id)``. Only
        session metadata is cached; raw event payloads never enter Redis.
        """
        if not sessions:
            return 0

        pipe = self.client.pipeline(transaction=True)
        timestamp = seen_at.astimezone(timezone.utc).isoformat()
        for session_id, (event_count, user_id) in sessions.items():
            key = f"{self.key_prefix}:session:{data_source_id}:{session_id}"
            mapping: dict[str, Any] = {"last_seen_at": timestamp}
            if user_id:
                mapping["user_id"] = user_id
            pipe.hset(key, mapping=mapping)
            pipe.hincrby(key, "event_count", event_count)
            pipe.expire(key, self.ttl_seconds)
        try:
            pipe.execute()
        except redis.RedisError:
            logger.warning("Redis session cache unavailable", exc_info=True)
            return 0
        return len(sessions)


class TrackingRequestProtection:
    """Applies configurable bot filtering and an atomic Redis rate limit."""

    def __init__(self, settings: Settings, client: Optional[Any] = None):
        self.settings = settings
        self.client = client or build_redis_client(settings)
        self.session_cache = RedisSessionCache(
            self.client,
            settings.tracking_redis_key_prefix,
            settings.tracking_session_ttl_seconds,
        )
        self.bot_patterns = tuple(
            pattern.strip().lower()
            for pattern in settings.tracking_bot_user_agent_patterns.split(",")
            if pattern.strip()
        )

    def ping(self) -> bool:
        """Report Redis reachability for /health.

        Returns False instead of raising: Redis backs rate limiting and session
        counters, both of which fail open, so an unreachable Redis is a degraded
        (not fatal) condition the health endpoint should surface without erroring.
        """
        try:
            return bool(self.client.ping())
        except redis.RedisError:
            logger.warning("Redis health probe failed", exc_info=True)
            return False

    def is_bot(self, user_agent: Optional[str]) -> bool:
        if not self.settings.tracking_bot_filter_enabled or not user_agent:
            return False
        normalized = user_agent.lower()
        return any(pattern in normalized for pattern in self.bot_patterns)

    def allow_request(self, request: Request) -> RateLimitDecision:
        """Consume one IP-window token, or apply the configured Redis fallback."""
        client_ip = request.client.host if request.client else "unknown"
        key = f"{self.settings.tracking_redis_key_prefix}:rate:ip:{client_ip}"
        try:
            count = int(
                self.client.eval(
                    _RATE_LIMIT_SCRIPT,
                    1,
                    key,
                    str(self.settings.tracking_rate_limit_window_seconds),
                )
            )
        except redis.RedisError:
            if self.settings.tracking_rate_limit_fail_open:
                logger.warning("Redis rate limiter unavailable; allowing request", exc_info=True)
                return RateLimitDecision(allowed=True)
            logger.error("Redis rate limiter unavailable; rejecting request", exc_info=True)
            return RateLimitDecision(
                allowed=False,
                retry_after_seconds=self.settings.tracking_rate_limit_window_seconds,
            )

        if count <= self.settings.tracking_rate_limit_requests:
            return RateLimitDecision(allowed=True)
        return RateLimitDecision(
            allowed=False,
            retry_after_seconds=self.settings.tracking_rate_limit_window_seconds,
        )


def build_redis_client(settings: Settings) -> Any:
    """Build a short-timeout Redis client; connections are opened on demand."""
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
        health_check_interval=30,
    )
