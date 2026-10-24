"""
ORM model for leo_ads.tenant.

Every ad-serving table (ad, campaign, creative, placement, ...) carries a
tenant_id foreign key into this table. It is intentionally minimal: customer360-promotions
only needs it so SQLAlchemy can resolve those foreign keys, tenant management
itself is out of scope for this service.
"""

import uuid
from typing import Optional

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from model.base import Base


class Tenant(Base):
    """Row in leo_ads.tenant. See db-schema-init.sql for the canonical DDL."""

    __tablename__ = "tenant"
    __table_args__ = {"schema": "leo_ads"}

    tenant_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    settings: Mapped[dict] = mapped_column(
        "settings", JSONB, nullable=False, default=dict
    )
    # Link to the platform tenant (customer360.sys_tenant.tenant_id). Lets the
    # admin API resolve this ad tenant from a caller's token. Nullable and
    # unique where set; added by sql-scripts/personalization-schema.sql.
    customer360_tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
