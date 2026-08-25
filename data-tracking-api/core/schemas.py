"""Request and response schemas for tracking-log ingestion."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class TrackingLogRequest(BaseModel):
    data_source_id: UUID
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    user_id: str | None = Field(default=None, min_length=1, max_length=256)
    events: list[dict[str, Any]] = Field(min_length=1)


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
