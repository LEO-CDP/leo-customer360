"""Pydantic schemas for CdpRawEvent (see core/models/events.py)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, model_validator


class EventCreate(BaseModel):
    tenant_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    domain: str

    # Optional direct link. If omitted, API auto-finds/creates a raw profile.
    raw_profile_id: Optional[uuid.UUID] = None
    master_profile_id: Optional[uuid.UUID] = None

    # Identity hints used to resolve or create cdp_raw_profiles_stage row.
    email: Optional[str] = None
    phone_number: Optional[str] = None
    external_customer_id: Optional[str] = None
    device_id: Optional[str] = None
    advertising_id: Optional[str] = None
    cookie_id: Optional[str] = None
    session_id: Optional[str] = None
    event_dedup_key: Optional[str] = None

    source_system: str
    event_dedup_key: Optional[str] = None
    channel: Optional[str] = None
    platform: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    event_category: str = "GENERAL"
    event_name: str
    is_conversion: bool = False

    entity_type: Optional[str] = None
    entity_id: Optional[str] = None

    event_value: Optional[Decimal] = None
    currency: Optional[str] = None

    transaction_id: Optional[str] = None
    transaction_status: Optional[str] = None

    location_code: Optional[str] = None
    location_name: Optional[str] = None

    event_time: Optional[datetime] = None
    event_payload: Optional[dict] = None

    @model_validator(mode="after")
    def validate_identity_linkage(self):
        if self.raw_profile_id is not None:
            return self

        identity_values = (
            self.email,
            self.phone_number,
            self.external_customer_id,
            self.device_id,
            self.advertising_id,
            self.cookie_id,
            self.session_id,
        )
        if any(v is not None and str(v).strip() != "" for v in identity_values):
            return self

        raise ValueError(
            "raw_profile_id is required unless at least one identity field is provided "
            "(email, phone_number, external_customer_id, device_id, advertising_id, cookie_id, session_id)."
        )


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: uuid.UUID
    event_time: datetime
    tenant_id: uuid.UUID
    domain: str
    master_profile_id: Optional[uuid.UUID] = None
    raw_profile_id: uuid.UUID

    external_customer_id: Optional[str] = None
    device_id: Optional[str] = None
    session_id: Optional[str] = None

    source_system: str
    channel: Optional[str] = None
    platform: Optional[str] = None

    event_category: str
    event_name: str
    is_conversion: bool

    entity_type: Optional[str] = None
    entity_id: Optional[str] = None

    event_value: Optional[Decimal] = None
    currency: Optional[str] = None

    transaction_id: Optional[str] = None
    transaction_status: Optional[str] = None

    location_name: Optional[str] = None
    event_payload: Optional[dict] = None
    created_at: Optional[datetime] = None
