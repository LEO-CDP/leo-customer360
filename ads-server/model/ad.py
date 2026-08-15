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
    Stable ad delivery object.

    PostgreSQL:

        leo_ads.ad
    """

    __tablename__ = "ad"
    __table_args__ = {
        "schema": "leo_ads",
    }

    ad_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("leo_ads.tenant.tenant_id"),
        nullable=False,
    )

    ad_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    campaign_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "leo_ads.campaign.campaign_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    creative_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "leo_ads.creative.creative_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    placement_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "leo_ads.placement.placement_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
    )

    score_weight: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
    )

    frequency_cap: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )