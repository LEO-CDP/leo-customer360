"""Pydantic schemas for system metadata entities."""

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SourceTypeValue = Literal[1, 2, 3, 4, 5]


class DataSourceBase(BaseModel):
    tenant_id: uuid.UUID
    name: str
    slug: str
    source_type: SourceTypeValue = 2
    status: Optional[int] = 1
    data_source_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    collect_directly: Optional[bool] = True
    first_party_data: Optional[bool] = True
    journey_level: Optional[int] = 3
    journey_map_id: Optional[str] = None
    touchpoint_hub_id: Optional[str] = None
    security_code: Optional[str] = None
    total_tracked_event: Optional[int] = 0
    avg_daily_event: Optional[int] = 0
    avg_events_per_profile: Optional[float] = 0
    access_tokens: Optional[dict] = None
    data_source_hosts: Optional[list[str]] = None
    javascript_tags: Optional[list[str]] = None
    qr_code_data: Optional[dict] = None


class DataSourceCreate(DataSourceBase):
    pass


class DataSourceUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    source_type: Optional[SourceTypeValue] = None
    status: Optional[int] = None
    data_source_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    collect_directly: Optional[bool] = None
    first_party_data: Optional[bool] = None
    journey_level: Optional[int] = None
    journey_map_id: Optional[str] = None
    touchpoint_hub_id: Optional[str] = None
    security_code: Optional[str] = None
    total_tracked_event: Optional[int] = None
    avg_daily_event: Optional[int] = None
    avg_events_per_profile: Optional[float] = None
    access_tokens: Optional[dict] = None
    data_source_hosts: Optional[list[str]] = None
    javascript_tags: Optional[list[str]] = None
    qr_code_data: Optional[dict] = None


class DataSourceRead(DataSourceBase):
    model_config = ConfigDict(from_attributes=True)

    data_source_id: uuid.UUID
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


ModelTypeValue = Literal[
    "classification",
    "regression",
    "clustering",
    "rules_engine",
    "generative_llm",
]
ModelStatusValue = Literal["ACTIVE", "INACTIVE", "TRAINING", "DEPRECATED", "FAILED"]


class ScoringModelBase(BaseModel):
    scoring_model_name: str = Field(..., max_length=100)
    display_name: str = Field(..., max_length=255)
    description: Optional[str] = None
    model_type: ModelTypeValue
    status: ModelStatusValue = "ACTIVE"
    schedule_definition: Optional[str] = Field(default=None, max_length=100)
    input_features: Optional[list[str]] = Field(default_factory=list)
    hyperparameters: Optional[dict[str, Any]] = Field(default_factory=dict)


class ScoringModelCreate(ScoringModelBase):
    pass


class ScoringModelUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    model_type: Optional[ModelTypeValue] = None
    status: Optional[ModelStatusValue] = None
    schedule_definition: Optional[str] = Field(default=None, max_length=100)
    input_features: Optional[list[str]] = None
    hyperparameters: Optional[dict[str, Any]] = None


class ScoringModelRead(ScoringModelBase):
    model_config = ConfigDict(from_attributes=True)

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
