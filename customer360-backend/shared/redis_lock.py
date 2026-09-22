"""Small Redis lease primitive shared by backend Dagster jobs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
_REFRESH_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


@dataclass
class RedisLease:
    """Ownership token for a renewable Redis lock."""

    client: Any
    key: str
    token: str
    ttl_seconds: int

    def refresh(self) -> None:
        """Extend the lease or fail closed if another worker owns the key."""
        refreshed = self.client.eval(
            _REFRESH_SCRIPT,
            1,
            self.key,
            self.token,
            str(self.ttl_seconds),
        )
        if int(refreshed) != 1:
            raise RuntimeError(f"Redis lease was lost for {self.key}")

    def release(self) -> None:
        """Release only this worker's lease."""
        try:
            self.client.eval(_RELEASE_SCRIPT, 1, self.key, self.token)
        except Exception:  # noqa: BLE001 - TTL remains the recovery mechanism.
            logger.warning("Could not release Redis lease %s; waiting for TTL", self.key)


def acquire_redis_lease(client: Any, key: str, ttl_seconds: int) -> Optional[RedisLease]:
    """Acquire a lease without blocking; return ``None`` when already owned."""
    ttl = max(1, int(ttl_seconds))
    token = uuid4().hex
    if not client.set(key, token, nx=True, ex=ttl):
        return None
    return RedisLease(client=client, key=key, token=token, ttl_seconds=ttl)
