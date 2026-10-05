"""Validated TSV rows for asynchronous content-item imports."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ContentType = Literal["article", "news", "video"]


class ContentImportRecord(BaseModel):
    """Canonical content row imported into cdp_content_items."""

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=1, max_length=100)
    item_type: ContentType
    title: str = Field(min_length=1, max_length=1000)
    summary: str | None = None
    image_url: str | None = Field(default=None, max_length=2048)
    cta_label: str | None = Field(default=None, max_length=255)
    cta_url: str | None = Field(default=None, max_length=2048)
    segment_tags: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    status_code: Literal[0, 1] = 1

    @field_validator("domain", "title")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("summary", "image_url", "cta_label", "cta_url")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("image_url", "cta_url")
    @classmethod
    def validate_http_urls(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("must use http or https")
        return value

    @field_validator("segment_tags")
    @classmethod
    def normalize_tags(cls, tags: list[str]) -> list[str]:
        normalized = [tag.strip() for tag in tags]
        if any(not tag for tag in normalized):
            raise ValueError("segment_tags must not contain blank values")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def require_timezone_for_published_at(self) -> ContentImportRecord:
        if self.published_at is not None and self.published_at.utcoffset() is None:
            raise ValueError("published_at must include a timezone")
        return self

    def to_content_item(
        self,
        *,
        tenant_id: UUID,
        content_item_id: UUID,
    ) -> dict:
        """Map the validated source row to the database content item shape."""
        return {
            "content_item_id": content_item_id,
            "tenant_id": tenant_id,
            "domain": self.domain,
            "item_type": self.item_type,
            "title": self.title,
            "summary": self.summary,
            "image_url": self.image_url,
            "cta_label": self.cta_label,
            "cta_url": self.cta_url,
            "segment_tags": self.segment_tags,
            "published_at": self.published_at,
            "status_code": self.status_code,
        }
