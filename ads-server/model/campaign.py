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
    Buying/business object.

    Campaign owns commercial configuration.

    Ad owns delivery configuration.
    """

    __tablename__ = "campaign"
    __table_args__ = {
        "schema": "leo_ads",
    }

    campaign_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    advertiser_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    source_account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    campaign_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    objective: Mapped[str | None] = mapped_column(
        String(60),
        nullable=True,
    )

    buying_model: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )

    budget_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )

    currency: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
    )

    daily_budget_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
    )

    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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
