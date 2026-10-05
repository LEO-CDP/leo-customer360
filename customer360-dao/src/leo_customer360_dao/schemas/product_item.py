"""API contracts for manually managed product items."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProductItemCreate(BaseModel):
    """Product source fields plus customer-facing content to create atomically."""

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=1, max_length=100)
    product_type: str = Field(min_length=1, max_length=100)
    source_id: str = Field(default="", max_length=255)
    source_type: str = Field(default="", max_length=100)
    product_id_type: str = Field(min_length=1, max_length=100)
    product_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=1000)
    summary: str | None = None
    image_url: str | None = Field(default=None, max_length=2048)
    cta_label: str | None = Field(default="View product", max_length=255)
    cta_url: str | None = Field(default=None, max_length=2048)
    keywords: list[str] = Field(default_factory=list)
    ext_attributes: dict[str, Any] = Field(default_factory=dict)
    original_price: Decimal | None = Field(default=None, ge=0)
    sale_price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")

    @field_validator("domain", "product_type", "product_id_type", "product_id", "title")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("source_id", "source_type")
    @classmethod
    def strip_optional_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("image_url", "cta_url")
    @classmethod
    def validate_urls(cls, value: str | None) -> str | None:
        if value is not None and value and not value.startswith(("http://", "https://")):
            raise ValueError("must use http or https")
        return value

    @model_validator(mode="after")
    def validate_product_metadata(self) -> ProductItemCreate:
        if self.product_type == "STOCK":
            if self.domain != "banking":
                raise ValueError("STOCK products must use the banking domain")
        elif self.product_type != f"{self.domain}_product":
            raise ValueError("product_type must match the <domain>_product value")
        if not self.source_id and self.product_type != "STOCK":
            raise ValueError("source_id is required for non-stock products")
        if (self.original_price is not None or self.sale_price is not None) and not self.currency:
            raise ValueError("currency is required when a product price is provided")
        if any(not keyword.strip() for keyword in self.keywords):
            raise ValueError("keywords must not contain blank values")
        try:
            json.dumps(self.ext_attributes, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("ext_attributes must contain finite JSON-compatible values") from exc
        return self


class ProductItemRead(BaseModel):
    """Product metadata plus generated presentation fields when available."""

    model_config = ConfigDict(from_attributes=True)

    product_item_id: str
    tenant_id: uuid.UUID
    content_item_id: uuid.UUID | None
    domain: str
    item_type: Literal["product"] = "product"
    product_type: str
    source_id: str
    source_type: str
    product_id_type: str
    product_id: str
    keywords: list[str]
    ext_attributes: dict[str, Any]
    original_price: Decimal | None
    sale_price: Decimal | None
    currency: str | None
    title: str
    summary: str | None
    image_url: str | None
    cta_label: str | None
    cta_url: str | None
    status_code: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
