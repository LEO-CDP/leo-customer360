"""Validated source-product rows used by asynchronous catalog imports."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_SOURCE_FIELDS_CHARACTERS = 32_000


class ProductImportRecord(BaseModel):
    """Canonical product record parsed from a source TSV."""

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=1, max_length=100)
    product_type: str = Field(min_length=1, max_length=100)
    source_id: str = Field(max_length=255)
    source_type: str = Field(default="", max_length=100)
    product_id_type: str = Field(min_length=1, max_length=100)
    product_id: str = Field(min_length=1, max_length=255)
    keywords: list[str] = Field(default_factory=list)
    ext_attributes: dict[str, Any] = Field(default_factory=dict)
    original_price: Decimal | None = Field(default=None, ge=0)
    sale_price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    image_url: str | None = Field(default=None, max_length=2048)
    product_url: str = Field(min_length=1, max_length=2048)
    source_fields: dict[str, str]

    @field_validator("domain", "product_type", "product_id_type", "product_id")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("source_id", "source_type")
    @classmethod
    def normalize_source_reference(cls, value: str) -> str:
        return value.strip()

    @field_validator("product_url", "image_url")
    @classmethod
    def validate_http_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("must use http or https")
        return value

    @model_validator(mode="after")
    def validate_price_currency(self) -> ProductImportRecord:
        if (self.original_price is not None or self.sale_price is not None) and not self.currency:
            raise ValueError("currency is required when a product price is provided")
        if not self.source_id and self.product_type != "STOCK":
            raise ValueError("source_id must not be blank for non-stock products")
        if any(not key.strip() for key in self.source_fields):
            raise ValueError("source_fields keys must not be blank")
        if any(len(key) > 200 for key in self.source_fields):
            raise ValueError("source_fields keys must not exceed 200 characters")
        if sum(
            len(key) + len(value) for key, value in self.source_fields.items()
        ) > MAX_SOURCE_FIELDS_CHARACTERS:
            raise ValueError(
                f"source_fields must not exceed {MAX_SOURCE_FIELDS_CHARACTERS} characters"
            )
        if self.product_type == "STOCK" and self.domain != "banking":
            raise ValueError("STOCK products must use the banking domain")
        if self.product_type != "STOCK" and self.product_type != f"{self.domain}_product":
            raise ValueError("product_type must match the <domain>_product value")
        try:
            json.dumps(self.ext_attributes, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("ext_attributes must contain finite JSON-compatible values") from exc
        return self

    @property
    def product_key(self) -> str:
        """Stable, tenant-local product identity used to reconcile LLM output."""
        return "\x1f".join(
            (self.source_type, self.source_id, self.product_id_type, self.product_id)
        )

    def to_content_item(
        self,
        *,
        tenant_id: UUID,
        content_item_id: UUID,
        title: str,
        summary: str,
        cta_label: str = "View product",
    ) -> dict[str, Any]:
        """Map generated presentation fields and retained source fields to the content table."""
        return {
            "content_item_id": content_item_id,
            "tenant_id": tenant_id,
            "domain": self.domain,
            "item_type": "product",
            "title": title,
            "summary": summary,
            "image_url": self.image_url,
            "cta_label": cta_label,
            "cta_url": self.product_url,
            "segment_tags": self.keywords,
        }
