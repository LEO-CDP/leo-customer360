"""Lightweight stand-in Table objects for ``sys_tenant`` / ``sys_organization``, plus
full ORM models for ``sys_user``, ``sys_userinfo``, ``sys_domain``, and others.

While this API may not own complete tenant administration, the full ORM-mapped
entities for `sys_user` and `sys_userinfo` have been included to support the 
authentication and SSO identity pipelines. `sys_tenant` and `sys_organization` 
remain lightweight stand-ins purely for ForeignKey resolution across the schema.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BIGINT, 
    Boolean, 
    CheckConstraint, 
    Column, 
    DateTime,
    ForeignKey, 
    SmallInteger, 
    Table, 
    Text, 
    UniqueConstraint,
    text
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import Base

# =============================================================================
# Lightweight Stand-in Tables for FK Resolution
# =============================================================================

sys_tenant_table = Table(
    "sys_tenant",
    Base.metadata,
    Column("tenant_id", PG_UUID(as_uuid=True), primary_key=True),
)

sys_organization_table = Table(
    "sys_organization",
    Base.metadata,
    Column("organization_id", PG_UUID(as_uuid=True), primary_key=True),
)

# =============================================================================
# Full ORM Models
# =============================================================================

class SysUser(Base):
    """Core application user/staff account.
    Stores identity and profile metadata (name, email, phone, etc.).
    Authentication/SSO credentials are decoupled into sys_userinfo.
    """

    __tablename__ = "sys_user"
    __table_args__ = (
        CheckConstraint("username = lower(username)", name="chk_sys_user_username_lower"),
        CheckConstraint("email IS NULL OR email = lower(email)", name="chk_sys_user_email_lower"),
        UniqueConstraint("tenant_id", "username", name="uq_username"),
        UniqueConstraint("tenant_id", "email", name="uq_email"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_organization.organization_id", ondelete="SET NULL")
    )
    
    username: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(Text)
    full_name: Mapped[Optional[str]] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(Text)
    job_title: Mapped[Optional[str]] = mapped_column(Text)
    department: Mapped[Optional[str]] = mapped_column(Text)
    language_code: Mapped[Optional[str]] = mapped_column(Text, server_default="en")
    timezone: Mapped[Optional[str]] = mapped_column(Text, server_default="UTC")
    
    status: Mapped[str] = mapped_column(Text, server_default="ACTIVE", nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(Text)
    
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, server_default=text("'{}'::jsonb"))
    
    # ORM relationship to linked SSO identities (1-to-many)
    sso_identities: Mapped[list["SysUserInfo"]] = relationship(
        "SysUserInfo", back_populates="user", cascade="all, delete-orphan"
    )


class SysUserInfo(Base):
    """User Login & SSO Identity Management.
    Handles multi-tenant SSO identities (Keycloak, Google, Microsoft) linked to a core sys_user account.
    """

    __tablename__ = "sys_userinfo"
    __table_args__ = (
        UniqueConstraint("tenant_id", "auth_provider", "provider_subject_id", name="uq_sys_userinfo_provider_id"),
        UniqueConstraint("tenant_id", "user_id", "auth_provider", name="uq_sys_userinfo_user_provider"),
    )

    userinfo_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id", ondelete="CASCADE"), nullable=False
    )
    
    auth_provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_subject_id: Mapped[str] = mapped_column(Text, nullable=False)
    
    access_token: Mapped[Optional[str]] = mapped_column(Text)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    status: Mapped[str] = mapped_column(Text, server_default="ACTIVE", nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, server_default=text("'{}'::jsonb"))
    
    # ORM relationship back to SysUser
    user: Mapped["SysUser"] = relationship("SysUser", back_populates="sso_identities")


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


class SysDataSource(Base):
    """Stores metadata and configuration for Data Sources/Connectors (e.g., access tokens, QR code data, webhook configs, journey routing) for data ingestion pipelines."""

    __tablename__ = "sys_data_source"
    __table_args__ = (
        CheckConstraint("source_type IN (1, 2, 3, 4, 5)", name="ck_sys_data_source_source_type"),
    )

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
    total_tracked_event: Mapped[int] = mapped_column(BIGINT, server_default="0")
    avg_daily_event: Mapped[int] = mapped_column(BIGINT, server_default="0")
    avg_events_per_profile: Mapped[float] = mapped_column(server_default="0.0")
    access_tokens: Mapped[Optional[dict]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    data_source_hosts: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), server_default=text("ARRAY[]::text[]"))
    javascript_tags: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), server_default=text("ARRAY[]::text[]"))
    qr_code_data: Mapped[Optional[dict]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(server_default=text("now()"))