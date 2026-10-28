"""Segmentation / Audience Builder and IAB Audience Taxonomy metadata (see
core-customer360/database-schema.sql: ``customer360.cdp_segments`` and
``customer360.cdp_audience_tags``).

One ``CdpSegment`` row per named segment tag -- the same tag strings that get
written into ``cdp_master_profiles.segmentation_tags`` (along with any mapped
IAB ``audience_tags`` written into ``cdp_master_profiles.audience_tags``).
``CdpAudienceTag`` stores the standardized IAB Audience Taxonomy 1.1 catalog
keyed by ``tag_name`` (slugified tier path).
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, SmallInteger, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from leo_customer360_dao.models.base import Base


class CdpAudienceTag(Base):
    """IAB Audience Taxonomy 1.1 catalog row keyed by ``tag_name``."""

    __tablename__ = "cdp_audience_tags"
    __table_args__ = (
        UniqueConstraint("taxonomy_version", "unique_id", name="uq_cdp_audience_tags_version_unique_id"),
    )

    tag_name: Mapped[str] = mapped_column(Text, primary_key=True)
    unique_id: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[Optional[str]] = mapped_column(Text)
    taxonomy_version: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'1.1'"))
    condensed_name: Mapped[str] = mapped_column(Text, nullable=False)
    tier_1: Mapped[str] = mapped_column(Text, nullable=False)
    tier_2: Mapped[Optional[str]] = mapped_column(Text)
    tier_3: Mapped[Optional[str]] = mapped_column(Text)
    tier_4: Mapped[Optional[str]] = mapped_column(Text)
    tier_5: Mapped[Optional[str]] = mapped_column(Text)
    tier_6: Mapped[Optional[str]] = mapped_column(Text)
    extension_notes: Mapped[Optional[str]] = mapped_column(Text)
    status_code: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))


class CdpSegment(Base):
    __tablename__ = "cdp_segments"

    segment_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    domain: Mapped[str] = mapped_column(Text, nullable=False, server_default="all")

    segment_tag: Mapped[str] = mapped_column(Text, nullable=False)
    audience_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("ARRAY[]::text[]")
    )
    segment_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    json_rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    sql_rules: Mapped[Optional[str]] = mapped_column(Text)
    final_generated_sql: Mapped[Optional[str]] = mapped_column(Text)
    processed_by: Mapped[str] = mapped_column(Text, nullable=False, server_default="human")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    member_count: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    last_computed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=False))

    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=False), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=False), server_default=text("now()"))
    status_code: Mapped[int] = mapped_column(SmallInteger, server_default="1")
