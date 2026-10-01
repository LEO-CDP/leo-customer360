"""Pydantic schemas for CdpSegment (see core/models/segmentation.py)."""

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from leo_customer360_dao.utils.sql_safety import validate_readonly_sql_statement, validate_sql_where_fragment


class SegmentBase(BaseModel):
    tenant_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    domain: str = Field(default="all")
    segment_tag: str
    segment_name: str
    description: Optional[str] = None
    json_rules: dict[str, Any] = Field(default_factory=dict)
    sql_rules: Optional[str] = None
    final_generated_sql: Optional[str] = None
    processed_by: str = Field(default="human", pattern="^(human|ai_agent)$")
    is_active: bool = True

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
