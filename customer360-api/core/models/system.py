"""Lightweight stand-in Table objects for ``sys_tenant`` / ``sys_user``, plus
full ORM models for ``sys_domain`` / ``sys_tenant_domain``.

This API doesn't own tenant/user administration (no full ORM-mapped
entities for them), but every crm_*/cdp_* model's
``ForeignKey("sys_tenant.tenant_id")`` / ``ForeignKey("sys_user.user_id")``
needs *some* ``Table`` registered in the shared ``Base.metadata`` to resolve
those FK targets -- SQLAlchemy sorts tables by FK dependency whenever an ORM
session flushes an INSERT/UPDATE, and raises ``NoReferencedTableError`` if
the referenced table was never declared, even though it's a valid schema
in PostgreSQL itself.

Columns beyond the primary key are intentionally omitted from those two
stand-ins: nothing ever queries through them, they only need to exist for FK
resolution. ``SysDomain`` / ``SysTenantDomain`` below are real ORM models
(mirroring ``database-schema.sql``'s "Business Domain Master" / "Tenant
Business Domains" sections) since core/routers/metadata.py's
``/metadata/domains`` endpoint actually queries them.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BIGINT, Boolean, Column, ForeignKey, SmallInteger, Table, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base

sys_tenant_table = Table(
    "sys_tenant",
    Base.metadata,
    Column("tenant_id", PG_UUID(as_uuid=True), primary_key=True),
)

sys_user_table = Table(
    "sys_user",
    Base.metadata,
    Column("user_id", PG_UUID(as_uuid=True), primary_key=True),
)

# sys_domain for business domain catalog (e.g. retail, banking, travel)
class SysDomain(Base):
    """System-defined business domain catalog (e.g. retail, banking, travel)."""

    __tablename__ = "sys_domain"

    domain_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    domain_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    domain_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    icon: Mapped[Optional[str]] = mapped_column(Text)
    color: Mapped[Optional[str]] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    is_system: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(server_default=text("now()"))
    # Named `metadata_` (not `metadata`) to avoid clashing with the
    # reserved `Base.metadata` attribute; still maps to the `metadata` column.
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, server_default=text("'{}'::jsonb"))


# sys_tenant_domain: which sys_domain rows a given tenant has enabled
class SysTenantDomain(Base):
    """Join table: which sys_domain rows a given tenant has enabled."""

    __tablename__ = "sys_tenant_domain"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), primary_key=True
    )
    domain_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_domain.domain_id"), primary_key=True
    )
    is_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=text("now()"))
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, server_default=text("'{}'::jsonb"))


# sys_data_source: metadata/configuration for Data Sources/Connectors (e.g., access tokens, QR code data, webhook configs, journey routing) for data ingestion pipelines
class SysDataSource(Base):
    """Stores metadata and configuration for Data Sources/Connectors (e.g., access tokens, QR code data, webhook configs, journey routing) for data ingestion pipelines."""

    __tablename__ = "sys_data_source"

    data_source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[int] = mapped_column(SmallInteger, server_default="2")
    status: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    data_source_url: Mapped[Optional[str]] = mapped_column(Text)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text)
    collect_directly: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    first_party_data: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    journey_level: Mapped[int] = mapped_column(SmallInteger, server_default="3")
    journey_map_id: Mapped[Optional[str]] = mapped_column(Text)
    touchpoint_hub_id: Mapped[Optional[str]] = mapped_column(Text)
    security_code: Mapped[Optional[str]] = mapped_column(Text)
    estimated_total_event: Mapped[int] = mapped_column(BIGINT, server_default="0")
    access_tokens: Mapped[Optional[dict]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    data_source_hosts: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), server_default=text("ARRAY[]::text[]"))
    javascript_tags: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), server_default=text("ARRAY[]::text[]"))
    qr_code_data: Mapped[Optional[dict]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(server_default=text("now()"))