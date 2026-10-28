"""Optional Redis helpers used by DAO read-through caches."""

import logging
from typing import Optional

import redis

from leo_customer360_dao.config import settings

logger = logging.getLogger(__name__)

_client: Optional["redis.Redis"] = None
_client_initialized = False


def get_redis_client() -> Optional["redis.Redis"]:
    """Return a lazy Redis client, or ``None`` when caching is unavailable."""
    global _client, _client_initialized
    if not settings.cache_enabled:
        return None
    if not _client_initialized:
        _client_initialized = True
        try:
            _client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password or None,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        except Exception:
            logger.warning("Failed to initialize Redis client; caching disabled.", exc_info=True)
            _client = None
    return _client