"""Pydantic schemas for CdpContentItem (see core/models/content.py)."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ContentItemBase(BaseModel):
    tenant_id: uuid.UUID
    # Validated against sys_domain ('all' + active domain codes) via
    # core.utils.domains.validate_domain_value in the router.
    domain: str = "all"
    item_type: str = Field(pattern="^(news|video|product|article)$")
    title: str
    summary: Optional[str] = None
    image_url: Optional[str] = None
    cta_label: Optional[str] = None
    cta_url: Optional[str] = None
    segment_tags: Optional[list[str]] = None
    published_at: Optional[datetime] = None


class ContentItemCreate(ContentItemBase):
    pass


class ContentItemUpdate(BaseModel):
    domain: Optional[str] = None
    item_type: Optional[str] = Field(default=None, pattern="^(news|video|product|article)$")
    title: Optional[str] = None
    summary: Optional[str] = None
    image_url: Optional[str] = None
    cta_label: Optional[str] = None
    cta_url: Optional[str] = None
    segment_tags: Optional[list[str]] = None
    published_at: Optional[datetime] = None
    status_code: Optional[int] = None


class ContentItemRead(ContentItemBase):
    model_config = ConfigDict(from_attributes=True)
    content_item_id: uuid.UUID
    status_code: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class RecommendedContentItem(ContentItemRead):
    matched_tags: list[str] = Field(default_factory=list)
