"""Purging a visitor's fast-access (Redis) state for an erasure request.

The ad server (customer360-promotions) keeps per-visitor hot state under keys of
the form

    c360ads:<ad tenant id>:visitor:<visitor key as hex>:<what>

(``personalization/hot_state.py``: ``make_key`` and ``visitor_key_name``;
``HotState.purge_visitor`` deletes by exactly the pattern built here). Because the
two services do not share code, the format is pinned by
``tests/test_ad_hot_state.py``; change it in both places or that test fails.

The pattern ends in the visitor's own key, so it can reach neither another
visitor nor another tenant.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

KEY_PREFIX = "c360ads"
_SCAN_COUNT = 200


def visitor_key_pattern(ad_tenant_id: int, user_key: bytes) -> str:
    """The Redis SCAN pattern matching everything held for one visitor."""
    if isinstance(ad_tenant_id, bool) or not isinstance(ad_tenant_id, int):
        raise ValueError("ad_tenant_id must be an int")
    if not user_key:
        raise ValueError("a visitor key is required")
    return f"{KEY_PREFIX}:{ad_tenant_id}:visitor:{bytes(user_key).hex()}:*"


def purge_visitor(client: Any, ad_tenant_id: int, user_key: bytes) -> int:
    """Delete every key held for the visitor. Returns how many were removed."""
    pattern = visitor_key_pattern(ad_tenant_id, user_key)
    removed = 0
    batch: list = []
    for key in client.scan_iter(match=pattern, count=_SCAN_COUNT):
        batch.append(key)
        if len(batch) >= _SCAN_COUNT:
            removed += int(client.delete(*batch))
            batch = []
    if batch:
        removed += int(client.delete(*batch))
    return removed


def get_hot_state_client() -> Optional[Any]:
    """The Redis client for the ad server's hot state, or None if there is none.

    ``AD_PERSONALIZATION_REDIS_HOST`` (with optional ``_PORT``, ``_DB`` and
    ``_PASSWORD``) points at the ad server's Redis when it differs from the API's
    own cache. Otherwise the API's existing client is reused, which is correct
    when both services share one Redis.
    """
    host = os.environ.get("AD_PERSONALIZATION_REDIS_HOST", "").strip()
    if host:
        import redis

        return redis.Redis(
            host=host,
            port=int(os.environ.get("AD_PERSONALIZATION_REDIS_PORT", "6379")),
            db=int(os.environ.get("AD_PERSONALIZATION_REDIS_DB", "0")),
            password=os.environ.get("AD_PERSONALIZATION_REDIS_PASSWORD") or None,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    from leo_customer360_dao.cache import get_redis_client

    return get_redis_client()
