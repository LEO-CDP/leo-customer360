"""Validation and response contracts for campaign and creative management."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def validate_json_object(value: dict[str, Any]) -> dict[str, Any]:
    """Reject non-finite values that PostgreSQL JSONB cannot represent."""
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("must contain valid finite JSON") from exc
    return value


class CampaignInput(BaseModel):
    """Fields managed on a promotions campaign."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    campaign_key: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=255)
    advertiser_id: int | None = Field(default=None, gt=0, le=9223372036854775807)
    source_account_id: int | None = Field(default=None, gt=0, le=9223372036854775807)
    objective: str | None = Field(default=None, max_length=60)
    buying_model: str | None = Field(default=None, max_length=40)
    budget_amount: Decimal | None = Field(default=None, max_digits=20, decimal_places=6)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    daily_budget_amount: Decimal | None = Field(default=None, max_digits=20, decimal_places=6)
    status: Literal["draft", "active", "paused", "completed", "archived"] = "draft"
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_metadata = field_validator("metadata")(validate_json_object)

    @model_validator(mode="after")
    def check_dates(self) -> CampaignInput:
        """Validate date order without assuming a timezone for naive inputs."""
        for value in (self.starts_at, self.ends_at):
            if value is not None and value.utcoffset() is None:
                raise ValueError("dates must include a timezone")
        if self.starts_at and self.ends_at and self.ends_at < self.starts_at:
            raise ValueError("ends_at must not precede starts_at")
        return self


class CreativeInput(BaseModel):
    """Fields managed on a promotions creative."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    creative_key: str = Field(min_length=1, max_length=200)
    ad_type: str = Field(min_length=1, max_length=80)
    format_code: str = Field(min_length=1, max_length=80)
    campaign_id: int | None = Field(default=None, gt=0, le=9223372036854775807)
    advertiser_id: int | None = Field(default=None, gt=0, le=9223372036854775807)
    source_asset_id: int | None = Field(default=None, gt=0, le=9223372036854775807)
    render_type_code: str | None = Field(default=None, max_length=40)
    status: Literal["draft", "active", "paused", "archived"] = "active"
    version_no: int = Field(default=1, gt=0, le=2147483647)
    priority: int = Field(default=0, ge=-2147483648, le=2147483647)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    headline: str | None = None
    subheadline: str | None = None
    body: str | None = None
    cta: str | None = Field(default=None, max_length=255)
    image_url: str | None = None
    video_url: str | None = None
    logo_url: str | None = None
    content_payload: dict[str, Any] = Field(default_factory=dict)

    _validate_content_payload = field_validator("content_payload")(validate_json_object)

    @model_validator(mode="after")
    def check_dates(self) -> CreativeInput:
        """Validate date order without assuming a timezone for naive inputs."""
        for value in (self.starts_at, self.ends_at):
            if value is not None and value.utcoffset() is None:
                raise ValueError("dates must include a timezone")
        if self.starts_at and self.ends_at and self.ends_at < self.starts_at:
            raise ValueError("ends_at must not precede starts_at")
        return self


class CampaignRead(CampaignInput):
    """Campaign representation returned by the management API."""

    campaign_id: int
    tenant_id: int
    created_at: datetime
    updated_at: datetime


class CreativeRead(CreativeInput):
    """Creative representation returned by the management API."""

    creative_id: int
    tenant_id: int
    created_at: datetime
    updated_at: datetime


class Customer360CampaignInput(BaseModel):
    """Source-owned campaign fields synchronized from Customer 360."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=255)
    objective: str | None = Field(default=None, max_length=60)
    budget_amount: Decimal | None = Field(default=None, max_digits=20, decimal_places=6)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    metadata: dict[str, Any] = Field(default_factory=dict)
    _validate_metadata = field_validator("metadata")(validate_json_object)


class Customer360CreativeInput(BaseModel):
    """Source-owned creative fields synchronized from Customer 360."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    ad_type: str = Field(min_length=1, max_length=80)
    format_code: str = Field(min_length=1, max_length=80)
    headline: str = Field(min_length=1)
    body: str | None = None
    image_url: str | None = None
    cta: str | None = Field(default=None, max_length=255)
    content_payload: dict[str, Any] = Field(default_factory=dict)
    _validate_content_payload = field_validator("content_payload")(validate_json_object)
