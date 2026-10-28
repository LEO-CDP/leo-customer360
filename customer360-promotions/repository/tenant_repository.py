"""
Tenant lookup repository.

Responsible for resolving a public tenant key (the ``tenant`` parameter the
widget and callers send) to the internal numeric ``tenant_id`` in leo_ads.tenant.

Persistence only. Positive results are cached in-process for a short time
because every serve, event and consent request needs the lookup; a tenant that
is paused or archived stops resolving once the entry expires. Unknown or
inactive tenants are never cached, so a newly created tenant works immediately.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker


class TenantRepository:
    """Resolve tenant keys to ids."""

    def __init__(
        self,
        engine: Engine,
        cache_ttl_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.engine = engine
        self._ttl = cache_ttl_seconds
        self._clock = clock
        self._cache: dict[str, tuple[int, float]] = {}
        self._session_factory = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )

    def get_active_tenant_id(self, tenant_key: str) -> Optional[int]:
        """The id of the active tenant with this key, or None."""
        if not tenant_key:
            return None

        cached = self._cache.get(tenant_key)
        if cached is not None and self._clock() < cached[1]:
            return cached[0]

        with self._session_factory() as session:
            tenant_id = session.execute(
                text(
                    """
                    SELECT tenant_id
                    FROM leo_ads.tenant
                    WHERE tenant_key = :tenant_key AND status = 'active'
                    """
                ),
                {"tenant_key": tenant_key},
            ).scalar_one_or_none()

        if tenant_id is None:
            self._cache.pop(tenant_key, None)
            return None

        self._cache[tenant_key] = (int(tenant_id), self._clock() + self._ttl)
        return int(tenant_id)
