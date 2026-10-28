"""
Campaign ORM model.

Maps to:

    leo_ads.campaign
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from model.base import Base


class Campaign(Base):
    """
    Campaign defines business/buying configuration.

    Campaign is separate from Ad for architectural clarity:

    - Campaign: Controls budget, dates, business rules, and objectives
    - Ad: Controls delivery configuration, frequency, and ranking
    - Creative: Controls content and rendering

    This separation allows multiple delivery configurations (Ads) to share
    the same business objectives (Campaign) and content (Creatives).

    Database:
        Schema: leo_ads
        Table:  campaign
        Indexes: tenant_id, status, starts_at, ends_at (for date range queries)

    Multi-tenancy:
        All queries must filter by tenant_id.
    """

    __tablename__ = "campaign"
    __table_args__ = {
        "schema": "leo_ads",
    }

    # PRIMARY KEY
    campaign_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )
    # Use campaign_key for external references.

    # TENANT ISOLATION
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    # ⚠️  CRITICAL: All queries must include tenant_id predicate.

    # ADVERTISER & ACCOUNT
    advertiser_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    # Links to the advertiser/publisher who owns this campaign.

    source_account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    # Maps to source DSP/platform account (e.g., Google Ads, internal API).

    # EXTERNAL IDENTIFIER
    campaign_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    # Use this instead of campaign_id for APIs and external systems.

    # DISPLAY/DESCRIPTION
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    # Human-readable campaign name.

    # BUSINESS OBJECTIVE
    objective: Mapped[str | None] = mapped_column(
        String(60),
        nullable=True,
    )
    # Examples: 'awareness', 'traffic', 'conversions', 'retention'.

    # PRICING MODEL
    buying_model: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )
    # Examples: 'CPM', 'CPC', 'CPA', 'vCPM', 'fixed'.

    # BUDGET CONFIGURATION
    budget_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    # Total budget for campaign lifetime.

    currency: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
    )
    # ISO 4217 currency code (e.g., 'USD', 'EUR').

    daily_budget_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    # Maximum to spend per day (if set).

    # CAMPAIGN STATUS
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
    )
    # Values: 'draft' | 'approved' | 'running' | 'paused' | 'ended' | 'archived'.
    # Only 'running' campaigns deliver ads.

    # CAMPAIGN DATES
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # When the campaign becomes eligible to serve.

    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # When the campaign stops serving (if set).

    # EXTENSIBLE CONFIGURATION
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    # Store campaign-specific attributes (e.g., targeting rules, constraints).

    # AUDIT TIMESTAMPS
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # When the campaign was created.

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # When the campaign was last modified.
