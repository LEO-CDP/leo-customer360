"""CRM-style entity models: Campaign, Lead, Contact, Account, Opportunity, Industry.

These mirror the ``ENTITY TABLES`` section of core-customer360/database-schema.sql.
The ``embedding vector(1536)`` columns are mapped via pgvector's SQLAlchemy
``Vector`` type for completeness, but are excluded from the default API
response schemas (see core/schemas/crm.py) to keep responses lightweight.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base


class Campaign(Base):
    __tablename__ = "crm_campaign"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    campaign_code: Mapped[Optional[str]] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[Optional[str]] = mapped_column(String(50), server_default="Draft")
    channel: Mapped[Optional[str]] = mapped_column(String(100))
    platform: Mapped[Optional[str]] = mapped_column(String(100))
    objective: Mapped[Optional[str]] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(Text)
    keywords: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    lang: Mapped[Optional[str]] = mapped_column(Text, server_default="en")
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536))
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    budget_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    currency: Mapped[Optional[str]] = mapped_column(String(3), server_default="VND")
    # Email-marketing links + human-approval gate.
    segment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_segments.segment_id", ondelete="SET NULL")
    )
    template_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("crm_email_templates.template_id", ondelete="SET NULL")
    )
    approval_status: Mapped[Optional[str]] = mapped_column(String(50), server_default="Draft")
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    strategy_summary: Mapped[Optional[str]] = mapped_column(Text)
    ai_plan: Mapped[Optional[dict]] = mapped_column(JSONB)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))


class CRMCampaignPerformanceDaily(Base):
    __tablename__ = "crm_campaign_performance_daily"

    performance_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("crm_campaign.campaign_id", ondelete="CASCADE"), nullable=False
    )
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    spend: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), server_default=text("0.00"))
    impressions: Mapped[Optional[int]] = mapped_column(BigInteger, server_default=text("0"))
    clicks: Mapped[Optional[int]] = mapped_column(BigInteger, server_default=text("0"))
    conversions: Mapped[Optional[int]] = mapped_column(BigInteger, server_default=text("0"))
    revenue_estimated: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), server_default=text("0.00"))
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))


class VwCampaignPerformanceMetrics(Base):
    """Read-only mapped class for customer360.vw_campaign_performance_metrics."""

    __tablename__ = "vw_campaign_performance_metrics"
    __table_args__ = {"info": {"is_view": True}}

    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    campaign_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    campaign_code: Mapped[Optional[str]] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(String(50))
    channel: Mapped[Optional[str]] = mapped_column(String(100))
    platform: Mapped[Optional[str]] = mapped_column(String(100))
    objective: Mapped[Optional[str]] = mapped_column(String(100))
    total_spend: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    total_impressions: Mapped[int] = mapped_column(BigInteger)
    total_clicks: Mapped[int] = mapped_column(BigInteger)
    total_conversions: Mapped[int] = mapped_column(BigInteger)
    total_revenue: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    ctr_percentage: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    cvr_percentage: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    cpa: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    roas: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class CampaignMember(Base):
    __tablename__ = "crm_campaign_member"

    campaign_member_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    campaign_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("crm_campaign.campaign_id")
    )
    contact_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True))
    status: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    keywords: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    lang: Mapped[Optional[str]] = mapped_column(Text, server_default="en")
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536))
    joined_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)


class Lead(Base):
    __tablename__ = "crm_lead"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    lead_source_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("crm_lead_source.lead_source_id", ondelete="SET NULL")
    )
    first_name: Mapped[Optional[str]] = mapped_column(Text)
    last_name: Mapped[Optional[str]] = mapped_column(Text)
    email: Mapped[Optional[str]] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    keywords: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    lang: Mapped[Optional[str]] = mapped_column(Text, server_default="en")
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536))
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)


class LeadSource(Base):
    __tablename__ = "crm_lead_source"

    lead_source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    keywords: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    lang: Mapped[Optional[str]] = mapped_column(Text, server_default="en")
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536))
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)


class Contact(Base):
    __tablename__ = "crm_contact"

    contact_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    first_name: Mapped[Optional[str]] = mapped_column(Text)
    last_name: Mapped[Optional[str]] = mapped_column(Text)
    email: Mapped[Optional[str]] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(Text)
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True))
    description: Mapped[Optional[str]] = mapped_column(Text)
    keywords: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    lang: Mapped[Optional[str]] = mapped_column(Text, server_default="en")
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536))
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)


class Account(Base):
    __tablename__ = "crm_account"

    account_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    industry_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True))
    description: Mapped[Optional[str]] = mapped_column(Text)
    keywords: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    lang: Mapped[Optional[str]] = mapped_column(Text, server_default="en")
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536))
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)


class Opportunity(Base):
    __tablename__ = "crm_opportunity"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("crm_account.account_id")
    )
    name: Mapped[Optional[str]] = mapped_column(Text)
    value: Mapped[Optional[float]] = mapped_column(Numeric)
    stage: Mapped[Optional[str]] = mapped_column(Text)
    close_date: Mapped[Optional[date]] = mapped_column(Date)
    description: Mapped[Optional[str]] = mapped_column(Text)
    keywords: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    lang: Mapped[Optional[str]] = mapped_column(Text, server_default="en")
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536))
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)


class Industry(Base):
    __tablename__ = "crm_industry"

    industry_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    keywords: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    lang: Mapped[Optional[str]] = mapped_column(Text, server_default="en")
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536))
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)


# ---------------------------------------------------------------------------
# Agentic Email Marketing entities.
# Mirror the "Agentic Email Marketing Schema" section of database-schema.sql.
# ---------------------------------------------------------------------------


class EmailTemplate(Base):
    __tablename__ = "crm_email_templates"

    template_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(Text)
    html_body: Mapped[Optional[str]] = mapped_column(Text)
    text_body: Mapped[Optional[str]] = mapped_column(Text)
    variables: Mapped[Optional[dict]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="Draft")
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))


class CampaignContentItem(Base):
    __tablename__ = "crm_campaign_content_items"

    campaign_content_item_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("crm_campaign.campaign_id", ondelete="CASCADE"), nullable=False
    )
    content_item_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_content_items.content_item_id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    role: Mapped[Optional[str]] = mapped_column(String(50))
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))


class SegmentSyncRun(Base):
    __tablename__ = "crm_segment_sync_runs"

    sync_run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False)
    segment_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_segments.segment_id", ondelete="CASCADE"), nullable=False
    )
    triggered_by: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="Pending")
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    customer_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lead_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    contact_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    finished_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)


class CampaignDispatchLog(Base):
    """Per-recipient email send ledger written by the email_engine job
. UNIQUE(campaign_id, master_profile_id) makes re-runs
    idempotent. Read-only from the API (dispatch-log evidence endpoint)."""

    __tablename__ = "cdp_campaign_dispatch_logs"

    dispatch_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("crm_campaign.campaign_id", ondelete="CASCADE"), nullable=False
    )
    master_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_master_profiles.master_profile_id", ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("crm_email_templates.template_id", ondelete="SET NULL")
    )
    recipient_email: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="Pending")
    provider: Mapped[Optional[str]] = mapped_column(String(100))
    provider_message_id: Mapped[Optional[str]] = mapped_column(Text)
    rendered_subject: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    run_id: Mapped[Optional[str]] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))


class EmailProviderConfig(Base):
    """Per-tenant, dynamically-managed email dispatch configuration.

    The email_engine resolves the active row at send time (Redis-cached, DB as
    source of truth) instead of reading static SMTP env vars, so a tenant's
    provider/credentials can change without a redeploy. ``smtp_password`` is a
    secret -- protect it at rest (pgcrypto / a secret manager) in any non-dev
    deployment; it is never returned by the read API."""

    __tablename__ = "crm_email_provider_config"

    config_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    provider: Mapped[str] = mapped_column(String(50), nullable=False, server_default="mock")
    smtp_host: Mapped[Optional[str]] = mapped_column(Text)
    smtp_port: Mapped[Optional[int]] = mapped_column(Integer)
    smtp_username: Mapped[Optional[str]] = mapped_column(Text)
    smtp_password: Mapped[Optional[str]] = mapped_column(Text)
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    from_address: Mapped[Optional[str]] = mapped_column(Text)
    from_name: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
