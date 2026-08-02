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
from sqlalchemy import BigInteger, Date, ForeignKey, Numeric, String, Text, text
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
