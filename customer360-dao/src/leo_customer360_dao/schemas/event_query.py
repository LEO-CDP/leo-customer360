"""Read models for tenant-scoped event queries backed by S3 Silver/RAW data."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class EventQueryRead(BaseModel):
    event_id: str
    event_time: datetime
    tenant_id: UUID
    domain: str
    master_profile_id: Optional[UUID] = None
    raw_profile_id: Optional[UUID] = None
    external_customer_id: Optional[str] = None
    device_id: Optional[str] = None
    session_id: Optional[str] = None
    source_system: Optional[str] = None
    channel: Optional[str] = None
    device_type: str = "unknown"
    platform: Optional[str] = None
    event_category: str = "GENERAL"
    event_name: Optional[str] = None
    is_conversion: bool = False
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    event_value: Optional[Decimal] = None
    currency: Optional[str] = None
    transaction_id: Optional[str] = None
    transaction_status: Optional[str] = None
    location_name: Optional[str] = None
    event_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None


class EventVolumeRead(BaseModel):
    """Number of events received during one UTC day."""

    day: date
    total: int = Field(ge=0)


class EventDeviceTypeVolumeRead(BaseModel):
    """Number of events grouped by normalized device type."""

    device_type: str
    total: int = Field(ge=0)
