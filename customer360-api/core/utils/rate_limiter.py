"""Redis-backed fixed-window rate limiter for brute-force-sensitive endpoints
(login attempts, repeated failed token validation).

Fails open if Redis is unavailable -- consistent with core/cache.py's
philosophy that Redis is a performance/protection optimization, never a hard
dependency for availability. This is a deliberate tradeoff: an attacker who
can also take down Redis loses throttling, but legitimate users never get
locked out by a Redis outage.
"""

import logging

from core.cache import get_redis_client

logger = logging.getLogger(__name__)


class RedisRateLimiter:
    """Fixed-window counter that only tracks *failures* (e.g. failed logins,
    failed token validation) for a key, so legitimate/successful requests are
    never throttled -- only repeated bad attempts from the same key are."""

    def __init__(self, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds

    def is_blocked(self, key: str) -> bool:
        """Read-only check: True if ``key`` has already exhausted its failed-
        attempt budget for the current window. Never increments the counter.
        Fails open (returns False) on any Redis error."""
        client = get_redis_client()
        raw = None
        if client is None:
            return False
        try:
            raw = client.get(f"ratelimit:{key}")
        except Exception:
            logger.warning("Rate limiter Redis GET failed for key=%s; failing open.", key, exc_info=True)
            return False
        return raw is not None and int(raw) >= self.max_attempts # type: ignore

    def record_failure(self, key: str) -> None:
        """Increments the failed-attempt counter for ``key``, starting a new
        window if none is active. Fails open (no-op) on any Redis error."""
        client = get_redis_client()
        if client is None:
            return
        redis_key = f"ratelimit:{key}"
        try:
            count = client.incr(redis_key)
            if count == 1:
                client.expire(redis_key, self.window_seconds)
        except Exception:
            logger.warning("Rate limiter Redis INCR failed for key=%s; failing open.", key, exc_info=True)
