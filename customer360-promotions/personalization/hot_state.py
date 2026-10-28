"""Fast per-visitor state and caches in Redis, failing open.

Redis is a rebuildable cache here, never the source of truth. Every call is
bounded by a short time budget, and any Redis problem (down, slow, mid-failover)
surfaces as one exception, :class:`HotStateUnavailable`, which callers turn into
default ranking. Nothing in this module may let a Redis error reach a visitor.

All keys carry the tenant id as a prefix (``c360ads:{tenant_id}:...``), so one
tenant's state can never be read for another. The same key format is used by
``customer360-api`` to purge a visitor on an erasure request.

:class:`InMemoryHotState` has the same interface and is used by tests, and by
development setups with no Redis.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

KEY_PREFIX = "c360ads"


class HotStateUnavailable(RuntimeError):
    """Redis is disabled, unreachable, or too slow. Callers must fall back."""


def make_key(tenant_id: int, *parts: Any) -> str:
    """Build a tenant-prefixed key, e.g. ``c360ads:7:visitor:ab12:counter:shown``."""
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int):
        raise ValueError("tenant_id must be an int")
    if not parts:
        raise ValueError("a key needs at least one part")
    rendered = []
    for part in parts:
        text = part.hex() if isinstance(part, (bytes, bytearray)) else str(part)
        if not text:
            raise ValueError("key parts must not be empty")
        rendered.append(text)
    return ":".join((KEY_PREFIX, str(tenant_id), *rendered))


SIGNAL_TTL_SECONDS = 2 * 86_400
SIGNAL_CAP = 200  # most recent signals kept per visitor


def _is_rate_limit_key(key: Any) -> bool:
    """Rate-limit counters (``...:counter:rate:...``) outlive a purge.

    They hold no behaviour, only "how many calls this minute", and removing them
    on a denial would let a visitor reset their own limit by alternating choices.
    """
    text = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
    return ":counter:rate:" in text


def signal_record(event: dict, features: list) -> tuple[str, str]:
    """``(field, json)`` for one signal: the event id, and what ranking needs from it."""
    value = {
        "ad": int(event["ad_id"]),
        "type": str(event["event_type"]),
        "at": float(event["at"]),
        "f": [[d, v] for d, v in features],
    }
    return str(event["event_id"]), json.dumps(value, separators=(",", ":"))


def parse_signals(raw_values: list) -> list:
    out = []
    for raw in raw_values:
        try:
            out.append(json.loads(raw))
        except (TypeError, ValueError):
            logger.warning("hot state: ignoring a corrupt signal")
    return out


def visitor_key_name(user_key: bytes, *parts: Any) -> tuple:
    """Key parts for one visitor's entry: ``visitor:<hex>:<parts...>``."""
    return ("visitor", bytes(user_key), *parts)


class HotState:
    """Redis-backed hot state. ``client`` is a ``redis.Redis`` or None (disabled)."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Any) -> "HotState":
        """Build from PromotionsSettings. Never connects until first use."""
        if not settings.cache_enabled:
            return cls(None)
        import redis

        budget = max(settings.personalization_time_budget_ms, 1) / 1000.0
        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password,
            socket_timeout=budget,
            socket_connect_timeout=budget,
            decode_responses=True,
        )
        return cls(client)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run(self, operation: Callable[[Any], Any]) -> Any:
        if self._client is None:
            raise HotStateUnavailable("Redis is disabled")
        try:
            return operation(self._client)
        except Exception as exc:  # noqa: BLE001 - redis raises many types; all mean "unavailable"
            raise HotStateUnavailable(str(exc)) from exc

    # ------------------------------------------------------------------
    # JSON values (settings cache, candidate cache, ...)
    # ------------------------------------------------------------------

    def get_json(self, tenant_id: int, *name: Any) -> Optional[Any]:
        key = make_key(tenant_id, *name)
        raw = self._run(lambda c: c.get(key))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            # The key name can contain a visitor key, so it is not logged.
            logger.warning("hot state: ignoring a corrupt value")
            return None

    def set_json(self, tenant_id: int, name: Iterable[Any], value: Any, ttl_seconds: int) -> None:
        key = make_key(tenant_id, *name)
        payload = json.dumps(value, separators=(",", ":"), default=str)
        self._run(lambda c: c.set(key, payload, ex=max(int(ttl_seconds), 1)))

    def delete(self, tenant_id: int, *name: Any) -> None:
        key = make_key(tenant_id, *name)
        self._run(lambda c: c.delete(key))

    # ------------------------------------------------------------------
    # Event de-duplication
    # ------------------------------------------------------------------

    def seen_event(self, tenant_id: int, event_id: str, ttl_seconds: int = 3600) -> bool:
        """Record an event id. Returns True if it was already seen (a duplicate)."""
        key = make_key(tenant_id, "event", event_id)
        created = self._run(lambda c: c.set(key, "1", nx=True, ex=max(int(ttl_seconds), 1)))
        return not created

    # ------------------------------------------------------------------
    # Counters (per visitor), with a sliding TTL
    # ------------------------------------------------------------------

    def incr_counter(
        self, tenant_id: int, user_key: bytes, name: str, ttl_seconds: int, amount: int = 1
    ) -> int:
        key = make_key(tenant_id, *visitor_key_name(user_key, "counter", name))

        def operation(client: Any) -> int:
            pipe = client.pipeline()
            pipe.incrby(key, amount)
            pipe.expire(key, max(int(ttl_seconds), 1))
            return int(pipe.execute()[0])

        return self._run(operation)

    def get_counters(self, tenant_id: int, user_key: bytes, names: list[str]) -> dict[str, int]:
        if not names:
            return {}
        keys = [make_key(tenant_id, *visitor_key_name(user_key, "counter", n)) for n in names]
        values = self._run(lambda c: c.mget(keys))
        return {n: int(v) if v is not None else 0 for n, v in zip(names, values)}

    # ------------------------------------------------------------------
    # Recent signals (fresh behaviour not yet folded into the durable profile)
    # ------------------------------------------------------------------

    def apply_event(
        self,
        tenant_id: int,
        user_key: bytes,
        event: dict,
        features: list,
        ttl_seconds: int = SIGNAL_TTL_SECONDS,
    ) -> bool:
        """Record one visitor signal. Returns False when this event id was already applied.

        Stored as a hash keyed by event id, so applying the same event again is a
        no-op and the set of signals does not depend on the order they arrive in.
        Every use of the set (``signals.py``) is a pure function of its contents.
        """
        key = make_key(tenant_id, *visitor_key_name(user_key, "signals"))
        field, value = signal_record(event, features)

        def operation(client: Any) -> bool:
            created = bool(client.hsetnx(key, field, value))
            client.expire(key, max(int(ttl_seconds), 1))
            if created and client.hlen(key) > SIGNAL_CAP:
                entries = client.hgetall(key)
                oldest = sorted(entries, key=lambda f: json.loads(entries[f]).get("at", 0))
                excess = oldest[: len(entries) - SIGNAL_CAP]
                if excess:
                    client.hdel(key, *excess)
            return created

        return self._run(operation)

    def get_signals(self, tenant_id: int, user_key: bytes) -> list:
        key = make_key(tenant_id, *visitor_key_name(user_key, "signals"))
        raw = self._run(lambda c: c.hvals(key))
        return parse_signals(raw)

    def delete_signals(self, tenant_id: int, user_key: bytes) -> None:
        self.delete(tenant_id, *visitor_key_name(user_key, "signals"))

    def purge_visitor(self, tenant_id: int, user_key: bytes) -> int:
        """Delete everything held for one visitor (signals, counters, ...). Returns keys removed.

        Every per-visitor key is ``<prefix>:<tenant>:visitor:<hex key>:...``, so one
        pattern covers them all and cannot reach another visitor or tenant. Used when
        a visitor denies consent, and mirrored by the admin erasure call.
        """
        pattern = make_key(tenant_id, "visitor", bytes(user_key), "*")

        def operation(client: Any) -> int:
            removed = 0
            batch: list = []
            for key in client.scan_iter(match=pattern, count=200):
                if _is_rate_limit_key(key):
                    continue
                batch.append(key)
                if len(batch) >= 200:
                    removed += int(client.delete(*batch))
                    batch = []
            if batch:
                removed += int(client.delete(*batch))
            return removed

        return self._run(operation)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        try:
            return bool(self._run(lambda c: c.ping()))
        except HotStateUnavailable:
            return False


class InMemoryHotState:
    """Same interface as :class:`HotState`, held in memory with TTLs.

    ``available = False`` makes every call raise :class:`HotStateUnavailable`,
    which is how tests exercise the fail-open paths.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._data: dict[str, tuple[Any, Optional[float]]] = {}
        self.available = True

    # -- internals -------------------------------------------------------

    def _guard(self) -> None:
        if not self.available:
            raise HotStateUnavailable("in-memory hot state marked unavailable")

    def _get(self, key: str) -> Any:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and self._clock() >= expires_at:
            del self._data[key]
            return None
        return value

    def _put(self, key: str, value: Any, ttl_seconds: Optional[int]) -> None:
        expires_at = None if ttl_seconds is None else self._clock() + max(int(ttl_seconds), 1)
        self._data[key] = (value, expires_at)

    # -- JSON values -----------------------------------------------------

    def get_json(self, tenant_id: int, *name: Any) -> Optional[Any]:
        self._guard()
        raw = self._get(make_key(tenant_id, *name))
        return None if raw is None else json.loads(raw)

    def set_json(self, tenant_id: int, name: Iterable[Any], value: Any, ttl_seconds: int) -> None:
        self._guard()
        self._put(make_key(tenant_id, *name), json.dumps(value, default=str), ttl_seconds)

    def delete(self, tenant_id: int, *name: Any) -> None:
        self._guard()
        self._data.pop(make_key(tenant_id, *name), None)

    # -- dedupe ----------------------------------------------------------

    def seen_event(self, tenant_id: int, event_id: str, ttl_seconds: int = 3600) -> bool:
        self._guard()
        key = make_key(tenant_id, "event", event_id)
        if self._get(key) is not None:
            return True
        self._put(key, "1", ttl_seconds)
        return False

    # -- counters --------------------------------------------------------

    def incr_counter(
        self, tenant_id: int, user_key: bytes, name: str, ttl_seconds: int, amount: int = 1
    ) -> int:
        self._guard()
        key = make_key(tenant_id, *visitor_key_name(user_key, "counter", name))
        value = int(self._get(key) or 0) + amount
        self._put(key, value, ttl_seconds)
        return value

    def get_counters(self, tenant_id: int, user_key: bytes, names: list[str]) -> dict[str, int]:
        self._guard()
        return {
            n: int(self._get(make_key(tenant_id, *visitor_key_name(user_key, "counter", n))) or 0)
            for n in names
        }

    # -- recent signals --------------------------------------------------

    def apply_event(
        self,
        tenant_id: int,
        user_key: bytes,
        event: dict,
        features: list,
        ttl_seconds: int = SIGNAL_TTL_SECONDS,
    ) -> bool:
        self._guard()
        key = make_key(tenant_id, *visitor_key_name(user_key, "signals"))
        field, value = signal_record(event, features)
        entries = dict(self._get(key) or {})
        created = field not in entries
        if created:
            entries[field] = value
            if len(entries) > SIGNAL_CAP:
                oldest = sorted(entries, key=lambda f: json.loads(entries[f]).get("at", 0))
                for f in oldest[: len(entries) - SIGNAL_CAP]:
                    del entries[f]
        self._put(key, entries, ttl_seconds)
        return created

    def get_signals(self, tenant_id: int, user_key: bytes) -> list:
        self._guard()
        key = make_key(tenant_id, *visitor_key_name(user_key, "signals"))
        return parse_signals(list((self._get(key) or {}).values()))

    def delete_signals(self, tenant_id: int, user_key: bytes) -> None:
        self.delete(tenant_id, *visitor_key_name(user_key, "signals"))

    def purge_visitor(self, tenant_id: int, user_key: bytes) -> int:
        self._guard()
        prefix = make_key(tenant_id, "visitor", bytes(user_key), "x")[:-1]  # "...visitor:<hex>:"
        doomed = [
            k for k in list(self._data) if k.startswith(prefix) and not _is_rate_limit_key(k)
        ]
        for key in doomed:
            del self._data[key]
        return len(doomed)

    # -- health / test helpers -------------------------------------------

    def ping(self) -> bool:
        return self.available

    def keys(self) -> list[str]:
        """All live keys (for tests asserting tenant prefixes)."""
        return [k for k in list(self._data) if self._get(k) is not None]
