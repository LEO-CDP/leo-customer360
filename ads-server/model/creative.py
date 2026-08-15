"""
Creative ORM models.

Maps to:

    leo_ads.creative
    leo_ads.creative_render
    leo_ads.destination
    leo_ads.tracking_endpoint
    leo_ads.creative_item
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from model.base import Base


class Creative(Base):
    """
    Canonical ad creative.

    Keeps common fields relational while provider/template-specific data
    lives in content_payload.
    """

    __tablename__ = "creative"
    __table_args__ = {
        "schema": "leo_ads",
    }

    creative_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    campaign_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    advertiser_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    source_asset_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    creative_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    ad_type: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )

    format_code: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )

    render_type_code: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
    )

    version_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    headline: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    subheadline: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    body: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    cta: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    image_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    video_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    logo_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    content_payload: Mapped[dict[str, Any]] = mapped_column(
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


class CreativeRender(Base):
    """
    Rendering configuration.

    This allows one creative to be rendered by different delivery mechanisms.
    """

    __tablename__ = "creative_render"
    __table_args__ = {
        "schema": "leo_ads",
    }

    creative_render_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    creative_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "leo_ads.creative.creative_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    render_type_code: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    template_key: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )

    loader_src: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    loader_async: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    container_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    container_class_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    render_config: Mapped[dict[str, Any]] = mapped_column(
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


class Destination(Base):
    """
    Creative destination.

    Examples:

        url
        product
        affiliate_url
        app_deep_link
    """

    __tablename__ = "destination"
    __table_args__ = {
        "schema": "leo_ads",
    }

    destination_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    creative_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "leo_ads.creative.creative_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    destination_type_code: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    final_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )


class TrackingEndpoint(Base):
    """
    Impression/click/conversion tracking endpoint.
    """

    __tablename__ = "tracking_endpoint"
    __table_args__ = {
        "schema": "leo_ads",
    }

    tracking_endpoint_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    creative_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "leo_ads.creative.creative_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    endpoint_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    method: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="GET",
    )

    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )


class CreativeItem(Base):
    """
    Product/carousel/recommendation item.

    Maps to:

        leo_ads.creative_item
    """

    __tablename__ = "creative_item"
    __table_args__ = {
        "schema": "leo_ads",
    }

    creative_item_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    creative_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "leo_ads.creative.creative_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    external_item_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    item_type: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        default="product",
    )

    item_name: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    subtitle: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    price_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )

    currency: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
    )

    original_price_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )

    discount_text: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
    )

    image_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    destination_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    highlight_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    item_payload: Mapped[dict[str, Any]] = mapped_column(
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
