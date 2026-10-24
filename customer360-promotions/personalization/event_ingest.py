"""Event ingest: validate, de-duplicate, screen and queue visitor ad interactions.

One batch in (``POST /events``), one acknowledgement out. Each event is checked on
its own, so one bad event rejects itself with a reason and never fails the batch.

Per event, in order:

    shape      invalid event type / request id / time           -> rejected
    ad         unknown for this tenant                          -> rejected ``unknown_ad``
    view       dwell below ``view_min_dwell_ms``                -> rejected ``below_view_threshold``
    identity   a stable ``event_id`` (derived when absent)
    duplicate  already seen (hot-state dedupe set)              -> rejected ``duplicate``
    quality    declared bot, orphan click, repeated click       -> STORED WITH A FLAG and reported
                                                                   rejected (``bot`` / ``orphan_click``
                                                                   / ``duplicate``) so tuning has evidence

A flagged event is excluded from profiles and statistics downstream.

Privacy: the visitor key is attached only when personalisation is on for the scope
AND the visitor is eligible under their consent state. Every other event is stored
with a null key (delivery statistics only) and never touches hot visitor state.

Durable writes go through a bounded buffer. If it cannot take the batch the whole
batch is refused (``IngestUnavailable``) after undoing the dedupe markers, so a
client retry is not mistaken for a duplicate.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid5

from personalization.buffered_writer import BufferOverflow
from personalization.consent import is_eligible
from personalization.eligibility import (
    SHOWN_COUNTER_TTL_SECONDS,
    parse_user_agent,
    shown_counter_name,
)
from personalization.features import ad_features
from personalization.hot_state import HotStateUnavailable
from personalization.identity import VisitorKeyUnavailable, describe_error, visitor_key
from personalization.signals import EVENT_WEIGHTS

logger = logging.getLogger(__name__)

EVENT_TYPES = ("impression", "view", "click", "dismiss", "conversion")

# Same declared-crawler list as customer360-event-api (TRACKING_BOT_USER_AGENT_PATTERNS).
BOT_USER_AGENT_PATTERNS = (
    "googlebot", "bingbot", "google-inspectiontool", "ahrefsbot", "semrushbot", "petalbot",
)

EVENT_ID_NAMESPACE = UUID("6f1d3a52-8c0e-4a63-9a14-5b7e2d9c0f31")

MAX_FUTURE = timedelta(minutes=10)
MAX_AGE = timedelta(days=7)
IMPRESSION_MARKER_TTL_SECONDS = 1800
CLICK_COLLAPSE_SECONDS = 5
DEDUPE_TTL_SECONDS = 3600
BUFFER_RETRY_AFTER_SECONDS = 5


class EventBatchTooLarge(ValueError):
    """More events than ``event_batch_max`` (HTTP 400)."""


class EventRateLimited(RuntimeError):
    """The visitor sent more events this minute than allowed (HTTP 429)."""

    retry_after = 60


class IngestUnavailable(RuntimeError):
    """The durable-write buffer is full or closed (HTTP 503)."""

    retry_after = BUFFER_RETRY_AFTER_SECONDS


@dataclass
class IngestResult:
    accepted: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"accepted": self.accepted, "rejected": self.rejected}


def is_declared_bot(user_agent: Optional[str]) -> bool:
    lowered = (user_agent or "").lower()
    return any(pattern in lowered for pattern in BOT_USER_AGENT_PATTERNS)


def derive_event_id(request_id: str, ad_key: str, event_type: str, sequence: int) -> str:
    """Deterministic UUIDv5, so a retried batch yields the same ids."""
    return str(uuid5(EVENT_ID_NAMESPACE, f"{request_id}:{ad_key}:{event_type}:{sequence}"))


def sanitise_page_url(value: Optional[str]) -> Optional[str]:
    """The page address without its query string or fragment (T097).

    Those parts routinely carry tokens, email addresses and campaign identifiers,
    and the engine only needs to know which page an ad was on. Anything that is not
    an http(s) address is dropped.
    """
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    host = parts.hostname or ""
    netloc = host + (f":{parts.port}" if parts.port else "")  # drops any user:password@
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))[:2048]


def _parse_uuid(value: Optional[str]) -> Optional[str]:
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _parse_time(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class EventIngest:
    """Validates and queues one batch of events for one tenant."""

    def __init__(
        self,
        *,
        ad_repository: Any,
        consent_repository: Any,
        settings_resolver: Any,
        hot_state: Any,
        writer: Any,
        config: Any,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._ads = ad_repository
        self._consent = consent_repository
        self._settings = settings_resolver
        self._hot_state = hot_state
        self._writer = writer
        self._config = config
        self._now = now
        self._wall_clock = wall_clock

    # ------------------------------------------------------------------

    def ingest(
        self,
        *,
        tenant_id: int,
        events: Sequence[Any],
        leovid: Optional[str] = None,
        session_id: Optional[str] = None,
        site: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> IngestResult:
        config = self._config
        if len(events) > config.event_batch_max:
            raise EventBatchTooLarge(f"at most {config.event_batch_max} events per call")

        now = self._now()
        user_key = self._profiling_key(tenant_id, leovid, site)
        self._check_rate_limit(
            tenant_id, user_key or self._anonymous_key(tenant_id, session_id), len(events)
        )

        ads = self._ads.get_ads_by_keys(tenant_id, sorted({e.ad_key for e in events}))
        bot = is_declared_bot(user_agent)
        device_type = parse_user_agent(user_agent)[0]
        session_uuid = _parse_uuid(session_id)

        result = IngestResult()
        rows: list[dict[str, Any]] = []
        seen_markers: list[str] = []
        sequence: dict[tuple, int] = {}
        batch_impressions: set[tuple[str, str]] = set()

        for index, event in enumerate(events):

            def reject(reason: str, index: int = index) -> None:
                result.rejected.append({"index": index, "reason": reason})

            if event.event_type not in EVENT_TYPES:
                reject("invalid_event_type")
                continue
            request_id = _parse_uuid(event.request_id)
            if request_id is None:
                reject("invalid_request_id")
                continue
            event_time = _parse_time(event.event_time)
            if event_time is None or not (now - MAX_AGE <= event_time <= now + MAX_FUTURE):
                reject("invalid_time")
                continue
            ad = ads.get(event.ad_key)
            if ad is None:
                reject("unknown_ad")
                continue
            if event.event_type == "view" and (event.dwell_ms or 0) < config.view_min_dwell_ms:
                reject("below_view_threshold")
                continue

            slot = (request_id, event.ad_key, event.event_type)
            sequence[slot] = sequence.get(slot, -1) + 1
            event_id = _parse_uuid(event.event_id) or derive_event_id(
                request_id, event.ad_key, event.event_type, sequence[slot]
            )

            if self._seen(tenant_id, event_id, seen_markers):
                reject("duplicate")
                continue

            flag = None
            if bot:
                flag = "bot"
            elif event.event_type == "click":
                if self._repeated_click(tenant_id, request_id, event.ad_key):
                    flag = "duplicate"
                elif not self._had_impression(tenant_id, request_id, event.ad_key, batch_impressions):
                    flag = "orphan_click"

            if event.event_type == "impression" and flag is None:
                batch_impressions.add((request_id, event.ad_key))
                self._record_impression(tenant_id, user_key, request_id, event.ad_key, ad, now)
            if flag is None and user_key is not None:
                self._apply_signal(tenant_id, user_key, event_id, event.event_type, event_time, ad)

            rows.append(
                {
                    "event_id": event_id,
                    "event_time": event_time,
                    "tenant_id": tenant_id,
                    "event_type": event.event_type,
                    "ad_id": ad["ad_id"],
                    "campaign_id": ad.get("campaign_id"),
                    "creative_id": ad.get("creative_id"),
                    "placement_id": ad.get("placement_id"),
                    "user_key_hash": user_key,
                    "request_id": request_id,
                    "session_id": session_uuid,
                    "device_type": device_type,
                    "page_url": sanitise_page_url(event.page_url),
                    "payload": {
                        k: v
                        for k, v in {
                            "dwell_ms": event.dwell_ms,
                            "click_id": event.click_id,
                            "site": site,
                            "session": None if session_uuid else session_id,
                        }.items()
                        if v is not None
                    },
                    "flags": [flag] if flag else [],
                }
            )
            if flag:
                reject(flag)
            else:
                result.accepted += 1

        self._queue(tenant_id, rows, seen_markers)
        return result

    # ------------------------------------------------------------------
    # Identity and rate limiting
    # ------------------------------------------------------------------

    def _profiling_key(
        self, tenant_id: int, leovid: Optional[str], site: Optional[str]
    ) -> Optional[bytes]:
        """The visitor key, only when this visitor may be profiled; otherwise None."""
        if not leovid:
            return None
        try:
            key = visitor_key(tenant_id, leovid)
        except (VisitorKeyUnavailable, ValueError):
            return None
        try:
            settings = self._settings.resolve(tenant_id, None, site)
            if not settings.enabled:
                return None
            status = self._consent.get_status(tenant_id, key)
            return key if is_eligible(status, settings.consent_mode).eligible else None
        except Exception as exc:  # noqa: BLE001 - if unsure, do not profile
            logger.error(
                "event ingest: could not resolve eligibility (%s); storing without a key",
                describe_error(exc),
            )
            return None

    @staticmethod
    def _anonymous_key(tenant_id: int, session_id: Optional[str]) -> Optional[bytes]:
        """A throw-away limiter key for visitors without one (never stored)."""
        if not session_id:
            return None
        return hashlib.sha256(f"{tenant_id}:{session_id}".encode()).digest()[:16]

    def _check_rate_limit(
        self, tenant_id: int, limiter_key: Optional[bytes], count: int
    ) -> None:
        if limiter_key is None:
            return
        window = int(self._wall_clock() // 60)
        try:
            total = self._hot_state.incr_counter(
                tenant_id, limiter_key, f"rate:events:{window}", ttl_seconds=120, amount=count
            )
        except HotStateUnavailable:
            return
        if total > self._config.event_rate_limit_per_minute:
            raise EventRateLimited("too many events")

    # ------------------------------------------------------------------
    # Hot-state helpers (all fail open)
    # ------------------------------------------------------------------

    def _seen(self, tenant_id: int, event_id: str, markers: list[str]) -> bool:
        try:
            if self._hot_state.seen_event(tenant_id, event_id, DEDUPE_TTL_SECONDS):
                return True
        except HotStateUnavailable:
            return False  # the database's ON CONFLICT DO NOTHING still de-duplicates
        markers.append(event_id)
        return False

    def _repeated_click(self, tenant_id: int, request_id: str, ad_key: str) -> bool:
        name = f"click:{request_id}:{ad_key}"
        try:
            return bool(self._hot_state.seen_event(tenant_id, name, CLICK_COLLAPSE_SECONDS))
        except HotStateUnavailable:
            return False

    def _had_impression(
        self, tenant_id: int, request_id: str, ad_key: str, batch: set[tuple[str, str]]
    ) -> bool:
        if (request_id, ad_key) in batch:
            return True
        try:
            return self._hot_state.get_json(tenant_id, "impression", request_id, ad_key) is not None
        except HotStateUnavailable:
            return True  # cannot tell, so do not flag

    def _record_impression(
        self,
        tenant_id: int,
        user_key: Optional[bytes],
        request_id: str,
        ad_key: str,
        ad: dict[str, Any],
        now: datetime,
    ) -> None:
        try:
            self._hot_state.set_json(
                tenant_id, ("impression", request_id, ad_key), 1, IMPRESSION_MARKER_TTL_SECONDS
            )
            if user_key is not None:
                self._hot_state.incr_counter(
                    tenant_id,
                    user_key,
                    shown_counter_name(ad["ad_id"], now),
                    SHOWN_COUNTER_TTL_SECONDS,
                )
        except HotStateUnavailable:
            logger.warning("event ingest: hot state unavailable; impression marker/counter skipped")

    def _apply_signal(
        self,
        tenant_id: int,
        user_key: bytes,
        event_id: str,
        event_type: str,
        event_time: datetime,
        ad: dict[str, Any],
    ) -> None:
        """Make a click, dismissal, view or conversion visible to the visitor's next request.

        Only called for an unflagged event of a visitor who may be profiled, and
        skipped (never failing the request) if hot state is unavailable: the batch
        profile job still folds the stored event in later.
        """
        if EVENT_WEIGHTS.get(event_type, 0.0) == 0.0:
            return
        try:
            self._hot_state.apply_event(
                tenant_id,
                user_key,
                {
                    "event_id": event_id,
                    "ad_id": ad["ad_id"],
                    "event_type": event_type,
                    "at": event_time.timestamp(),
                },
                ad_features(ad),
            )
        except HotStateUnavailable:
            logger.warning("event ingest: hot state unavailable; signal not applied")

    # ------------------------------------------------------------------

    def _queue(self, tenant_id: int, rows: list[dict[str, Any]], markers: list[str]) -> None:
        if not rows:
            return
        try:
            self._writer.put_many(rows)
        except BufferOverflow as exc:
            for event_id in markers:
                try:
                    self._hot_state.delete(tenant_id, "event", event_id)
                except HotStateUnavailable:
                    break
            raise IngestUnavailable(str(exc)) from exc
