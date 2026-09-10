"""Business service for tracking-log ingestion."""

from datetime import datetime, timezone
from typing import Any, Optional, Protocol
from uuid import UUID

from core.schemas import IDENTIFIER_FIELDS
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


class IdentityValidationError(ValueError):
    """Raised when a batch cannot be associated with a known identity."""


class TrackingLogService:
    """Coordinates request timestamps and durable object-storage writes."""

    def __init__(self, storage: TrackingLogStorage, session_cache: SessionCache):
        self.storage = storage
        self.session_cache = session_cache

    def ingest(
        self,
        data_source_id: UUID,
        events: list[dict[str, Any]],
        session_id: Optional[str] = None,
        anonymous_id: Optional[str] = None,
        device_id: Optional[str] = None,
        device_fingerprint: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> tuple[StoredTrackingLog, int]:
        received_at = datetime.now(timezone.utc)
        if not _has_identity(
            events,
            session_id=session_id,
            anonymous_id=anonymous_id,
            device_id=device_id,
            device_fingerprint=device_fingerprint,
            user_id=user_id,
        ):
            raise IdentityValidationError(
                "At least one session, anonymous, device, or user identifier is required"
            )

        events = _enrich_events(
            events,
            session_id=session_id,
            anonymous_id=anonymous_id,
            device_id=device_id,
            device_fingerprint=device_fingerprint,
            user_id=user_id,
            metadata=metadata,
        )
        stored = self.storage.store_tracking_logs(data_source_id, events, received_at)
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
        event_session_id = _string_value(event.get("session_id")) or session_id
        if not event_session_id:
            continue
        event_user_id = _string_value(event.get("user_id")) or user_id
        count, cached_user_id = sessions.get(event_session_id, (0, None))
        sessions[event_session_id] = (count + 1, event_user_id or cached_user_id)
    return sessions


def _enrich_events(
    events: list[dict[str, Any]],
    *,
    session_id: Optional[str],
    anonymous_id: Optional[str],
    device_id: Optional[str],
    device_fingerprint: Optional[str],
    user_id: Optional[str],
    metadata: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve batch-level identity metadata in each durable event record."""
    identities = {
        "session_id": session_id,
        "anonymous_id": anonymous_id,
        "device_id": device_id,
        "device_fingerprint": device_fingerprint,
        "user_id": user_id,
    }
    if not any(identities.values()) and not metadata:
        return events

    enriched_events = []
    for event in events:
        enriched_event = dict(event)
        for field_name, value in identities.items():
            if value:
                enriched_event.setdefault(field_name, value)
        if metadata:
            enriched_event.setdefault("metadata", dict(metadata))
        enriched_events.append(enriched_event)
    return enriched_events


def _has_identity(
    events: list[dict[str, Any]],
    **identities: Optional[str],
) -> bool:
    if any(identities.get(field_name) for field_name in IDENTIFIER_FIELDS):
        return True
    return any(
        any(event.get(field_name) for field_name in IDENTIFIER_FIELDS)
        for event in events
    )


def _string_value(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None
