"""
Ad ORM model.

Maps to:

    leo_ads.ad
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from model.base import Base


class Ad(Base):
    """
    Ad delivery configuration tied to a placement.

    This model represents the serving configuration for an ad unit.
    Ads are composed by linking campaigns (business logic), creatives (content),
    and placements (inventory). The Ad model itself is delivery-focused:

    - score_weight: Used by ranking systems to order candidates
    - frequency_cap: Prevents showing the same ad too frequently to a user
    - metadata: Extensible field for ad-tech-specific attributes
    - status: Ads must be active to serve

    Database:
        Schema: leo_ads
        Table:  ad
        Index:  placement_id, status, score_weight (hot path)

    Multi-tenancy:
        All queries must filter by tenant_id to prevent cross-tenant exposure.
    """

    __tablename__ = "ad"
    __table_args__ = {
        "schema": "leo_ads",
    }

# PRIMARY KEY
    ad_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )
    # ⚠️  Do NOT use ad_id to identify ads to end users.
    # Always use ad_key for external references.

    # TENANT ISOLATION
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("leo_ads.tenant.tenant_id"),
        nullable=False,
    )
    # ⚠️  CRITICAL: Every query must include tenant_id predicate.

    # EXTERNAL IDENTIFIER
    ad_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    # Use this for APIs and external systems instead of ad_id.

    # BUSINESS LOGIC REFERENCE (optional)
    campaign_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "leo_ads.campaign.campaign_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    # Nullable: ads may exist without a campaign (e.g., legacy or system ads).

    # CONTENT REFERENCE (required, protected)
    creative_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "leo_ads.creative.creative_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    # ondelete=RESTRICT prevents removing creatives while ads reference them.

    # INVENTORY REFERENCE (required, protected)
    placement_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "leo_ads.placement.placement_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    # ondelete=RESTRICT prevents removing placements while ads reference them.

    # SERVING STATUS
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
    )
    # Values: 'active' | 'paused' | 'archived'
    # Only 'active' ads are served. Paused ads are preserved for audit/history.

    # RANKING/PRIORITY
    score_weight: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
    )
    # Used by ranking service to order candidate ads. Higher = more likely to serve.

    # FREQUENCY/IMPRESSION LIMITS
    frequency_cap: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    # Max impressions per user in a time window. Enforced by the serving layer.\n
    # EXTENSIBLE CONFIGURATION
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    # Store ad-tech-specific attributes (e.g., external platform IDs, custom targeting).\n
    # AUDIT TIMESTAMPS
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # When the ad was added to the system.\n
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # When the ad was last modified.