"""Business service for tracking-log ingestion."""

from datetime import datetime, timezone
from typing import Any, Optional, Protocol
from uuid import UUID

from core.storage import StoredTrackingLog


class TrackingLogStorage(Protocol):
    def store_tracking_logs(
        self,
        data_source_id: UUID,
        events: list[dict[str, Any]],
        received_at: datetime,
    ) -> StoredTrackingLog: ...


class SessionCache(Protocol):
    def touch_sessions(
        self,
        data_source_id: UUID,
        sessions: dict[str, tuple[int, Optional[str]]],
        seen_at: datetime,
    ) -> int: ...


class EventStream(Protocol):
    def publish(
        self,
        data_source_id: UUID,
        events: list[dict[str, Any]],
        received_at: datetime,
    ) -> int: ...


class TrackingLogService:
    """Coordinates request timestamps and durable object-storage writes."""

    def __init__(
        self,
        storage: TrackingLogStorage,
        session_cache: SessionCache,
        event_stream: Optional[EventStream] = None,
    ):
        self.storage = storage
        self.session_cache = session_cache
        self.event_stream = event_stream

    def ingest(
        self,
        data_source_id: UUID,
        events: list[dict[str, Any]],
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> tuple[StoredTrackingLog, int]:
        received_at = datetime.now(timezone.utc)
        events = _enrich_events(events, session_id=session_id, user_id=user_id)
        stored = self.storage.store_tracking_logs(data_source_id, events, received_at)
        # Best-effort publish to the broker stream (consumed by the backend Loader). The S3
        # object above is the durable record, so a stream failure never affects the response.
        if self.event_stream is not None:
            self.event_stream.publish(data_source_id, events, received_at)
        sessions = _collect_sessions(events, session_id=session_id, user_id=user_id)
        cached_session_count = self.session_cache.touch_sessions(data_source_id, sessions, received_at)
        return stored, cached_session_count


def _collect_sessions(
    events: list[dict[str, Any]],
    *,
    session_id: Optional[str],
    user_id: Optional[str],
) -> dict[str, tuple[int, Optional[str]]]:
    """Aggregate session counters without copying event payloads to Redis."""
    sessions: dict[str, tuple[int, Optional[str]]] = {}
    for event in events:
        event_session_id = session_id or _string_value(event.get("session_id"))
        if not event_session_id:
            continue
        event_user_id = user_id or _string_value(event.get("user_id"))
        count, cached_user_id = sessions.get(event_session_id, (0, None))
        sessions[event_session_id] = (count + 1, event_user_id or cached_user_id)
    return sessions


def _enrich_events(
    events: list[dict[str, Any]],
    *,
    session_id: Optional[str],
    user_id: Optional[str],
) -> list[dict[str, Any]]:
    """Preserve batch-level identity metadata in each durable event record."""
    if not session_id and not user_id:
        return events

    enriched_events = []
    for event in events:
        enriched_event = dict(event)
        if session_id:
            enriched_event.setdefault("session_id", session_id)
        if user_id:
            enriched_event.setdefault("user_id", user_id)
        enriched_events.append(enriched_event)
    return enriched_events


def _string_value(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None
