"""Pydantic schemas for system metadata entities."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class DataSourceBase(BaseModel):
    tenant_id: uuid.UUID
    name: str
    slug: str
    source_type: Optional[int] = 2
    status: Optional[int] = 1
    data_source_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    collect_directly: Optional[bool] = True
    first_party_data: Optional[bool] = True
    journey_level: Optional[int] = 3
    journey_map_id: Optional[str] = None
    touchpoint_hub_id: Optional[str] = None
    security_code: Optional[str] = None
    estimated_total_event: Optional[int] = 0
    access_tokens: Optional[dict] = None
    data_source_hosts: Optional[list[str]] = None
    javascript_tags: Optional[list[str]] = None
    qr_code_data: Optional[dict] = None


class DataSourceCreate(DataSourceBase):
    pass


class DataSourceUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    source_type: Optional[int] = None
    status: Optional[int] = None
    data_source_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    collect_directly: Optional[bool] = None
    first_party_data: Optional[bool] = None
    journey_level: Optional[int] = None
    journey_map_id: Optional[str] = None
    touchpoint_hub_id: Optional[str] = None
    security_code: Optional[str] = None
    estimated_total_event: Optional[int] = None
    access_tokens: Optional[dict] = None
    data_source_hosts: Optional[list[str]] = None
    javascript_tags: Optional[list[str]] = None
    qr_code_data: Optional[dict] = None


class DataSourceRead(DataSourceBase):
    model_config = ConfigDict(from_attributes=True)

    data_source_id: uuid.UUID
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
