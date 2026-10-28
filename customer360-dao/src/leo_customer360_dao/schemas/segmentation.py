"""Pydantic schemas for CdpSegment and CdpAudienceTag (see models/segmentation.py)."""

import re
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from leo_customer360_dao.utils.sql_safety import validate_readonly_sql_statement, validate_sql_where_fragment


def slugify_tier_segment(text_value: str) -> str:
    """Convert one IAB taxonomy tier label into a normalized URL/tag-safe slug."""
    slug = text_value.strip().lower()
    slug = slug.replace("+", "-plus").replace("&", "and").replace("$", "usd-")
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug


def build_audience_tag_name(tiers: list[Optional[str]]) -> str:
    """Build a deterministic slugified tier path from IAB Audience Taxonomy tiers."""
    parts = [slugify_tier_segment(tier) for tier in tiers if tier and tier.strip()]
    parts = [part for part in parts if part]
    if not parts:
        raise ValueError("At least one non-empty tier is required to build tag_name")
    return "/".join(parts)


def normalize_audience_tag_list(values: Optional[list[str]]) -> list[str]:
    """Trim, filter blanks, and deduplicate audience tag names while preserving order."""
    if not values:
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise ValueError("Each audience tag must be a string")
        cleaned = item.strip()
        if not cleaned:
            continue
        if cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    return normalized


class AudienceTagBase(BaseModel):
    unique_id: str = Field(..., min_length=1)
    parent_id: Optional[str] = None
    taxonomy_version: str = Field(default="1.1", min_length=1)
    condensed_name: str = Field(..., min_length=1)
    tier_1: str = Field(..., min_length=1)
    tier_2: Optional[str] = None
    tier_3: Optional[str] = None
    tier_4: Optional[str] = None
    tier_5: Optional[str] = None
    tier_6: Optional[str] = None
    extension_notes: Optional[str] = None
    status_code: int = 1

    @field_validator("unique_id", "taxonomy_version", "condensed_name", "tier_1")
    @classmethod
    def _strip_required_str(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field cannot be blank")
        return cleaned

    @field_validator("parent_id", "tier_2", "tier_3", "tier_4", "tier_5", "tier_6", "extension_notes")
    @classmethod
    def _strip_optional_str(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned if cleaned else None


class AudienceTagCreate(AudienceTagBase):
    tag_name: Optional[str] = None

    @model_validator(mode="after")
    def _ensure_tag_name(self) -> "AudienceTagCreate":
        if self.tag_name is not None and self.tag_name.strip():
            self.tag_name = self.tag_name.strip()
        else:
            self.tag_name = build_audience_tag_name(
                [self.tier_1, self.tier_2, self.tier_3, self.tier_4, self.tier_5, self.tier_6]
            )
        return self


class AudienceTagUpdate(BaseModel):
    unique_id: Optional[str] = None
    parent_id: Optional[str] = None
    taxonomy_version: Optional[str] = None
    condensed_name: Optional[str] = None
    tier_1: Optional[str] = None
    tier_2: Optional[str] = None
    tier_3: Optional[str] = None
    tier_4: Optional[str] = None
    tier_5: Optional[str] = None
    tier_6: Optional[str] = None
    extension_notes: Optional[str] = None
    status_code: Optional[int] = None


class AudienceTagRead(AudienceTagBase):
    model_config = ConfigDict(from_attributes=True)
    tag_name: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AudienceTagImportResult(BaseModel):
    taxonomy_version: str
    total_rows_parsed: int
    upserted_count: int
    sample_tag_names: list[str] = Field(default_factory=list)


class SegmentBase(BaseModel):
    tenant_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    domain: str = Field(default="all")
    segment_tag: str
    audience_tags: list[str] = Field(default_factory=list)
    segment_name: str
    description: Optional[str] = None
    json_rules: dict[str, Any] = Field(default_factory=dict)
    sql_rules: Optional[str] = None
    final_generated_sql: Optional[str] = None
    processed_by: str = Field(default="human", pattern="^(human|ai_agent)$")
    is_active: bool = True

    @field_validator("audience_tags")
    @classmethod
    def _validate_audience_tags(cls, v: list[str]) -> list[str]:
        return normalize_audience_tag_list(v)

    @field_validator("sql_rules")
    @classmethod
    def _validate_sql_rules(cls, v: Optional[str]) -> Optional[str]:
        return validate_sql_where_fragment(v) if v else v

    @field_validator("final_generated_sql")
    @classmethod
    def _validate_final_generated_sql(cls, v: Optional[str]) -> Optional[str]:
        return validate_readonly_sql_statement(v) if v else v


class SegmentCreate(SegmentBase):
    pass


class SegmentUpdate(BaseModel):
    user_id: Optional[uuid.UUID] = None
    domain: Optional[str] = Field(default=None)
    segment_tag: Optional[str] = None
    audience_tags: Optional[list[str]] = None
    segment_name: Optional[str] = None
    description: Optional[str] = None
    json_rules: Optional[dict[str, Any]] = None
    sql_rules: Optional[str] = None
    final_generated_sql: Optional[str] = None
    processed_by: Optional[str] = Field(default=None, pattern="^(human|ai_agent)$")
    is_active: Optional[bool] = None
    member_count: Optional[int] = None
    last_computed_at: Optional[datetime] = None
    status_code: Optional[int] = None

    @field_validator("audience_tags")
    @classmethod
    def _validate_audience_tags(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return None
        return normalize_audience_tag_list(v)

    @field_validator("sql_rules")
    @classmethod
    def _validate_sql_rules(cls, v: Optional[str]) -> Optional[str]:
        return validate_sql_where_fragment(v) if v else v

    @field_validator("final_generated_sql")
    @classmethod
    def _validate_final_generated_sql(cls, v: Optional[str]) -> Optional[str]:
        return validate_readonly_sql_statement(v) if v else v


class SegmentRead(SegmentBase):
    model_config = ConfigDict(from_attributes=True)
    segment_id: uuid.UUID
    member_count: Optional[int] = None
    last_computed_at: Optional[datetime] = None
    status_code: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SegmentAudienceTagsRequest(BaseModel):
    audience_tags: list[str] = Field(default_factory=list)
    recompute_profiles: bool = True

    @field_validator("audience_tags")
    @classmethod
    def _validate_audience_tags(cls, v: list[str]) -> list[str]:
        return normalize_audience_tag_list(v)


# --------------------------------------------------------------------------- NL -> segment draft
class SegmentDraftRequest(BaseModel):
    """No tenant field on purpose: tenant comes from auth."""

    # Latest message: new description, answer, or refinement.
    description: str = Field(..., min_length=1, max_length=2000)
    domain: str = Field("all", min_length=1, max_length=64)
    # Multi-turn state echoed by the browser (untrusted): last turn's summary and question.
    so_far: Optional[str] = Field(None, max_length=2000)
    last_question: Optional[str] = Field(None, max_length=2000)
    # Builder rules, so "also only VIPs" edits them. Re-validated; ignored if invalid.
    current_rules: Optional[dict[str, Any]] = None


class SegmentDraftResult(BaseModel):
    """Draft for the Audience Builder; nothing saved, no SQL.

    ``valid``: load ``json_rules``. ``needs_clarification``: show ``question`` (and
    ``suggestions``). ``rejected``: ``question`` says why.
    """

    validation_status: Literal["valid", "needs_clarification", "rejected"]
    ready_for_segment_persistence: bool
    interpretation: str = ""
    json_rules: Optional[dict[str, Any]] = None
    segment_tag: Optional[str] = None
    segment_name: Optional[str] = None
    domain: str = "all"
    fields_used: list[str] = Field(default_factory=list)
    question: Optional[str] = None
    field: Optional[str] = None
    suggestions: list[str] = Field(default_factory=list)
    # Summary for the browser to show and send back next turn.
    so_far: Optional[str] = None
