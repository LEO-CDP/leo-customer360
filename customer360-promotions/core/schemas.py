"""
Request and response schemas for the personalisation endpoints.

Shapes follow specs/003-ad-personalization-engine/contracts/data-plane.openapi.yaml.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConsentChange(BaseModel):
    """Body of ``PUT /consent``: a visitor's choice for behaviour-based personalisation."""

    model_config = ConfigDict(extra="ignore")

    tenant: str = Field(min_length=1, max_length=100)
    leovid: str = Field(min_length=1, max_length=128)
    status: Literal["granted", "denied"]
    source: str = Field(default="widget", min_length=1, max_length=40)


class EventIn(BaseModel):
    """One interaction in a ``POST /events`` batch.

    ``event_type`` and the ids are plain strings here on purpose: a bad value
    rejects that one event with a reason in the acknowledgement instead of failing
    the whole batch with a schema error.
    """

    model_config = ConfigDict(extra="ignore")

    event_type: str = Field(max_length=40)
    request_id: str = Field(max_length=64)
    ad_key: str = Field(min_length=1, max_length=255)
    event_time: str = Field(max_length=64)
    event_id: Optional[str] = Field(default=None, max_length=64)
    placement_key: Optional[str] = Field(default=None, max_length=255)
    dwell_ms: Optional[int] = Field(default=None, ge=0, le=86_400_000)
    click_id: Optional[str] = Field(default=None, max_length=128)
    page_url: Optional[str] = Field(default=None, max_length=2048)


class EventBatch(BaseModel):
    """Body of ``POST /events``. The size limit is enforced by the service (400)."""

    model_config = ConfigDict(extra="ignore")

    tenant: str = Field(min_length=1, max_length=100)
    leovid: Optional[str] = Field(default=None, max_length=128)
    session_id: Optional[str] = Field(default=None, max_length=128)
    site: Optional[str] = Field(default=None, max_length=128)
    events: list[EventIn] = Field(min_length=1)


class ConversionIn(BaseModel):
    """Body of ``POST /conversions`` (a signed server-to-server postback).

    ``revenue_amount`` is a decimal string so it is never rounded in transit.
    """

    model_config = ConfigDict(extra="ignore")

    event_id: str = Field(min_length=1, max_length=64)
    conversion_time: str = Field(max_length=64)
    click_id: Optional[str] = Field(default=None, max_length=128)
    request_id: Optional[str] = Field(default=None, max_length=64)
    revenue_amount: Optional[str] = Field(
        default=None, pattern=r"^[0-9]+(\.[0-9]{1,8})?$", max_length=40
    )
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
