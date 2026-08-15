"""
Placement ORM models.

Maps to:

    leo_ads.placement
    leo_ads.placement_format
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from model.base import Base


class Placement(Base):
    """
    Publisher inventory placement.

    Example:

        homepage_top
        article_inline
        sidebar_300x600
        native_feed
    """

    __tablename__ = "placement"
    __table_args__ = {
        "schema": "leo_ads",
    }

    placement_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("leo_ads.tenant.tenant_id"),
        nullable=False,
    )

    placement_key: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
    )

    min_width_px: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    max_width_px: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    min_height_px: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    max_height_px: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    responsive: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
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


class PlacementFormat(Base):
    """
    Supported format/capability for a placement.

    Maps to:

        leo_ads.placement_format
    """

    __tablename__ = "placement_format"
    __table_args__ = {
        "schema": "leo_ads",
    }

    placement_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "leo_ads.placement.placement_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )

    format_code: Mapped[str] = mapped_column(
        String(80),
        primary_key=True,
    )

    width_px: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    height_px: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    width_unit: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="px",
    )

    height_unit: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="px",
    )

    responsive: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    constraints: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )