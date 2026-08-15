"""
ORM model for leo_ads.tenant.

Every ad-serving table (ad, campaign, creative, placement, ...) carries a
tenant_id foreign key into this table. It is intentionally minimal: ads-server
only needs it so SQLAlchemy can resolve those foreign keys, tenant management
itself is out of scope for this service.
"""

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
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
    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
