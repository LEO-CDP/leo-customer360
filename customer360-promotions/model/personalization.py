"""
ORM models for the ad personalisation tables.

Maps to:

    leo_ads.personalization_setting
    leo_ads.consent_record
    leo_ads.consent_history
    leo_ads.erasure_request

The canonical DDL is sql-scripts/personalization-schema.sql; design notes are
in specs/003-ad-personalization-engine/data-model.md. The constraints declared
here mirror the DDL so they are visible to tooling and tests, but the database
remains the source of truth.

Multi-tenancy:
    leo_ads has no row-level security. Every query on these tables must filter
    by tenant_id; the repositories in repository/ take it as a required argument.

The hot-path tables (visitor_behaviour_profile, ad_stat_hourly, cohort_pattern,
serving_decision) are accessed through plain SQL in their repositories and are
deliberately not mapped here.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from model.base import Base


class PersonalizationSetting(Base):
    """
    On/off switch, hold-out share and consent mode for a tenant, site or placement.

    The most specific row wins (placement, then site, then tenant). Personalisation
    ships OFF: ``enabled`` defaults to false. ``consent_mode`` is only meaningful on
    the tenant-scope row.
    """

    __tablename__ = "personalization_setting"
    __table_args__ = (
        CheckConstraint("scope_type IN ('tenant', 'site', 'placement')"),
        CheckConstraint("holdout_pct >= 0 AND holdout_pct <= 100"),
        CheckConstraint("consent_mode IN ('opt_in', 'opt_out')"),
        CheckConstraint("scope_type <> 'tenant' OR scope_ref = ''"),
        UniqueConstraint("tenant_id", "scope_type", "scope_ref"),
        {"schema": "leo_ads"},
    )

    setting_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # ⚠️  CRITICAL: Every query must include a tenant_id predicate.
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leo_ads.tenant.tenant_id"), nullable=False
    )

    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # '' for tenant scope; the opaque site_key for site; the placement_key for placement.
    scope_ref: Mapped[str] = mapped_column(Text, nullable=False, default="")

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    holdout_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("10.00")
    )
    holdout_salt: Mapped[str] = mapped_column(Text, nullable=False, default="default")
    consent_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="opt_in")

    updated_by: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConsentRecord(Base):
    """
    A visitor's current consent state for behaviour-based personalisation.

    No row means "no recorded choice": the tenant's consent mode decides.
    """

    __tablename__ = "consent_record"
    __table_args__ = (
        CheckConstraint("status IN ('granted', 'denied')"),
        {"schema": "leo_ads"},
    )

    # ⚠️  CRITICAL: Every query must include a tenant_id predicate.
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leo_ads.tenant.tenant_id"), primary_key=True
    )
    # Keyed hash of the visitor id; never the raw identifier.
    user_key_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)

    status: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="widget")
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConsentHistory(Base):
    """Append-only audit of every consent change, including when it changed."""

    __tablename__ = "consent_history"
    __table_args__ = (
        CheckConstraint("status IN ('granted', 'denied')"),
        {"schema": "leo_ads"},
    )

    history_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # ⚠️  CRITICAL: Every query must include a tenant_id predicate.
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leo_ads.tenant.tenant_id"), nullable=False
    )
    user_key_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    status: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="widget")
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ErasureRequest(Base):
    """
    Audit trail for data-subject erasure.

    A request is never deleted: it is the evidence that erasure happened and when.
    Urgent requests complete within 72 hours; all others within 30 days.
    Status moves pending -> redis_purged -> completed (or failed, then retried).
    """

    __tablename__ = "erasure_request"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'redis_purged', 'completed', 'failed')"),
        CheckConstraint("(status = 'completed') = (completed_at IS NOT NULL)"),
        CheckConstraint("status = 'completed' OR deleted_counts IS NULL"),
        {"schema": "leo_ads"},
    )

    erasure_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # ⚠️  CRITICAL: Every query must include a tenant_id predicate.
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leo_ads.tenant.tenant_id"), nullable=False
    )
    user_key_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    urgent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    requested_by: Mapped[Optional[str]] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Rows removed per table; set only at completion.
    deleted_counts: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text)
