"""Pydantic schemas for CRM-style entities (Campaign, Lead, Contact, ...).

Each entity has: Base (shared writable fields) -> Create -> Update (all
optional, for PATCH) -> Read (adds server-generated id/timestamps).
``embedding`` vector columns are intentionally omitted from all schemas to
keep API payloads small; they can be added back with an explicit opt-in
query param if ever needed.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CampaignBase(BaseModel):
    tenant_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    campaign_code: Optional[str] = None
    name: str
    status: Optional[str] = None
    channel: Optional[str] = None
    platform: Optional[str] = None
    objective: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = "en"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget_amount: Optional[Decimal] = None
    currency: Optional[str] = "VND"
    metadata_: Optional[dict] = None


class CampaignCreate(CampaignBase):
    pass


class CampaignUpdate(BaseModel):
    user_id: Optional[uuid.UUID] = None
    campaign_code: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None
    channel: Optional[str] = None
    platform: Optional[str] = None
    objective: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget_amount: Optional[Decimal] = None
    currency: Optional[str] = None
    metadata_: Optional[dict] = None


class CampaignRead(CampaignBase):
    model_config = ConfigDict(from_attributes=True)
    campaign_id: uuid.UUID
    created_at: Optional[datetime] = None


class CampaignMemberBase(BaseModel):
    tenant_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    campaign_id: Optional[uuid.UUID] = None
    contact_id: Optional[uuid.UUID] = None
    status: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = "en"
    metadata_: Optional[dict] = None


class CampaignMemberCreate(CampaignMemberBase):
    pass


class CampaignMemberUpdate(BaseModel):
    user_id: Optional[uuid.UUID] = None
    campaign_id: Optional[uuid.UUID] = None
    contact_id: Optional[uuid.UUID] = None
    status: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = None
    metadata_: Optional[dict] = None


class CampaignMemberRead(CampaignMemberBase):
    model_config = ConfigDict(from_attributes=True)
    campaign_member_id: uuid.UUID
    joined_at: Optional[datetime] = None


class LeadBase(BaseModel):
    tenant_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = "en"
    metadata_: Optional[dict] = None


class LeadCreate(LeadBase):
    pass


class LeadUpdate(BaseModel):
    user_id: Optional[uuid.UUID] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = None
    metadata_: Optional[dict] = None


class LeadRead(LeadBase):
    model_config = ConfigDict(from_attributes=True)
    lead_id: uuid.UUID
    created_at: Optional[datetime] = None


class LeadSourceBase(BaseModel):
    tenant_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    name: str
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = "en"
    metadata_: Optional[dict] = None


class LeadSourceCreate(LeadSourceBase):
    pass


class LeadSourceUpdate(BaseModel):
    user_id: Optional[uuid.UUID] = None
    name: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = None
    metadata_: Optional[dict] = None


class LeadSourceRead(LeadSourceBase):
    model_config = ConfigDict(from_attributes=True)
    lead_source_id: uuid.UUID


class ContactBase(BaseModel):
    tenant_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    account_id: Optional[uuid.UUID] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = "en"
    metadata_: Optional[dict] = None


class ContactCreate(ContactBase):
    pass


class ContactUpdate(BaseModel):
    user_id: Optional[uuid.UUID] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    account_id: Optional[uuid.UUID] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = None
    metadata_: Optional[dict] = None


class ContactRead(ContactBase):
    model_config = ConfigDict(from_attributes=True)
    contact_id: uuid.UUID
    created_at: Optional[datetime] = None


class AccountBase(BaseModel):
    tenant_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    name: str
    industry_id: Optional[uuid.UUID] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = "en"
    metadata_: Optional[dict] = None


class AccountCreate(AccountBase):
    pass


class AccountUpdate(BaseModel):
    user_id: Optional[uuid.UUID] = None
    name: Optional[str] = None
    industry_id: Optional[uuid.UUID] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = None
    metadata_: Optional[dict] = None


class AccountRead(AccountBase):
    model_config = ConfigDict(from_attributes=True)
    account_id: uuid.UUID
    created_at: Optional[datetime] = None


class OpportunityBase(BaseModel):
    tenant_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    account_id: Optional[uuid.UUID] = None
    name: Optional[str] = None
    value: Optional[float] = None
    stage: Optional[str] = None
    close_date: Optional[date] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = "en"
    metadata_: Optional[dict] = None


class OpportunityCreate(OpportunityBase):
    pass


class OpportunityUpdate(BaseModel):
    user_id: Optional[uuid.UUID] = None
    account_id: Optional[uuid.UUID] = None
    name: Optional[str] = None
    value: Optional[float] = None
    stage: Optional[str] = None
    close_date: Optional[date] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = None
    metadata_: Optional[dict] = None


class OpportunityRead(OpportunityBase):
    model_config = ConfigDict(from_attributes=True)
    opportunity_id: uuid.UUID
    created_at: Optional[datetime] = None


class IndustryBase(BaseModel):
    tenant_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    name: str
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = "en"
    metadata_: Optional[dict] = None


class IndustryCreate(IndustryBase):
    pass


class IndustryUpdate(BaseModel):
    user_id: Optional[uuid.UUID] = None
    name: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    lang: Optional[str] = None
    metadata_: Optional[dict] = None


class IndustryRead(IndustryBase):
    model_config = ConfigDict(from_attributes=True)
    industry_id: uuid.UUID


# ---------------------------------------------------------------------------
# Campaign Analytics Schemas (Dashboard / Phase 1)
# ---------------------------------------------------------------------------

class CampaignFilterParams(BaseModel):
    """Query filters matching UI Filter Bar controls."""

    search: Optional[str] = Field(None, description="Search term for campaign name or code")
    status: Optional[str] = Field(None, description="Active, Paused, Draft, etc.")
    channel: Optional[str] = Field(None, description="Paid Search, Paid Social, Organic, etc.")
    platform: Optional[str] = Field(None, description="Google, Meta, TikTok, Zalo, YouTube, etc.")
    objective: Optional[str] = Field(None, description="Leads, Conversions, App Install, Awareness, etc.")
    sort_by: str = Field("total_spend", description="Column to sort by")
    sort_order: str = Field("desc", description="asc or desc")
    page: int = Field(1, ge=1)
    page_size: int = Field(10, ge=1, le=100)


class CampaignMetricItem(BaseModel):
    """Table row from customer360.vw_campaign_performance_metrics."""

    model_config = ConfigDict(from_attributes=True)

    campaign_id: uuid.UUID
    campaign_code: Optional[str] = None
    name: str
    status: Optional[str] = None
    channel: Optional[str] = None
    platform: Optional[str] = None
    objective: Optional[str] = None
    total_spend: Decimal
    total_impressions: int
    total_clicks: int
    total_conversions: int
    total_revenue: Decimal
    ctr_percentage: Decimal
    cvr_percentage: Decimal
    cpa: Decimal
    roas: Decimal


class CampaignKPIResponse(BaseModel):
    """Aggregate KPI cards payload."""

    total_campaigns: int
    total_spend: Decimal
    total_impressions: int
    total_clicks: int
    overall_ctr: Decimal
    total_conversions: int
    overall_cvr: Decimal
    total_revenue: Decimal
    overall_roas: Decimal


class DailySpendTrendItem(BaseModel):
    """Chart item for Campaign Spend Trend."""

    report_date: date
    spend: Decimal


class TopCampaignItem(BaseModel):
    """Chart item for Top Campaigns by Conversions or ROAS."""

    campaign_id: uuid.UUID
    name: str
    conversions: int
    roas: Decimal


class PaginatedCampaignResponse(BaseModel):
    """Paginated campaign table output."""

    items: List[CampaignMetricItem]
    total: int
    page: int
    page_size: int
    total_pages: int
