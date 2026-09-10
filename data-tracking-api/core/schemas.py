"""Request and response schemas for tracking-log ingestion."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


IDENTIFIER_FIELDS = (
    "session_id",
    "anonymous_id",
    "device_id",
    "device_fingerprint",
    "user_id",
)
MAX_IDENTIFIER_LENGTH = 256


def normalize_identifier(value: Any, field_name: str) -> str | None:
    """Normalize an opaque identity value before it reaches storage or Redis."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    if len(normalized) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(
            f"{field_name} must be at most {MAX_IDENTIFIER_LENGTH} characters"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError(f"{field_name} contains control characters")
    return normalized


class TrackingLogRequest(BaseModel):
    data_source_id: UUID
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    anonymous_id: str | None = Field(default=None, min_length=1, max_length=256)
    device_id: str | None = Field(default=None, min_length=1, max_length=256)
    device_fingerprint: str | None = Field(default=None, min_length=1, max_length=256)
    user_id: str | None = Field(default=None, min_length=1, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(min_length=1)

    @field_validator(*IDENTIFIER_FIELDS, mode="before")
    @classmethod
    def validate_identifier(cls, value: Any, info: Any) -> str | None:
        return normalize_identifier(value, info.field_name)

    @field_validator("events")
    @classmethod
    def validate_event_identifiers(cls, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized_events = []
        for event in events:
            normalized_event = dict(event)
            for field_name in IDENTIFIER_FIELDS:
                if field_name in normalized_event:
                    normalized_event[field_name] = normalize_identifier(
                        normalized_event[field_name], field_name
                    )
            normalized_events.append(normalized_event)
        return normalized_events


class TrackingLogResponse(BaseModel):
    data_source_id: UUID
    accepted: bool = True
    filtered: bool = False
    filter_reason: str | None = None
    bucket: str | None = None
    object_key: str | None = None
    event_count: int
    received_at: datetime
    cached_session_count: int = 0
    queue_message_id: str | None = None
