"""Contracts for generating display content from imported product fields."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_SOURCE_FIELDS_CHARACTERS = 32_000


class ProductContentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_key: str = Field(min_length=1, max_length=700)
    source_fields: dict[str, str]

    @model_validator(mode="after")
    def validate_source_size(self) -> ProductContentInput:
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
        return self


class ProductContentGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    products: list[ProductContentInput] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_product_keys(self) -> ProductContentGenerationRequest:
        keys = [product.product_key for product in self.products]
        if len(keys) != len(set(keys)):
            raise ValueError("product_key values must be unique within a request")
        return self


class GeneratedProductContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_key: str = Field(min_length=1, max_length=700)
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=4000)

    @field_validator("title", "summary")
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("generated text must not be blank")
        return cleaned


class ProductContentGenerationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    products: list[GeneratedProductContent] = Field(min_length=1, max_length=20)
