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


APPROVAL_STATUS_PATTERN = "^(Draft|InReview|Approved|Rejected)$"


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
    # Email-marketing links + human-approval gate.
    segment_id: Optional[uuid.UUID] = None
    template_id: Optional[uuid.UUID] = None
    approval_status: Optional[str] = Field(default=None, pattern=APPROVAL_STATUS_PATTERN)
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    strategy_summary: Optional[str] = None
    ai_plan: Optional[dict] = None
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
    segment_id: Optional[uuid.UUID] = None
    template_id: Optional[uuid.UUID] = None
    approval_status: Optional[str] = Field(default=None, pattern=APPROVAL_STATUS_PATTERN)
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    strategy_summary: Optional[str] = None
    ai_plan: Optional[dict] = None
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
    lead_source_id: Optional[uuid.UUID] = None
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
    lead_source_id: Optional[uuid.UUID] = None
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
# Agentic Email Marketing Schemas
# ---------------------------------------------------------------------------

SYNC_RUN_STATUS_PATTERN = "^(Pending|Running|Completed|Failed)$"


class EmailTemplateBase(BaseModel):
    tenant_id: uuid.UUID
    name: str
    subject: Optional[str] = None
    html_body: Optional[str] = None
    text_body: Optional[str] = None
    variables: dict = Field(default_factory=dict)
    # NOT NULL DEFAULT 'Draft' in the DB, so a real default (not None) is used
    # here -- the generic create path does model_dump() without exclude_unset.
    status: str = Field(default="Draft", pattern=APPROVAL_STATUS_PATTERN)
    created_by: Optional[uuid.UUID] = None
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    metadata_: Optional[dict] = None


class EmailTemplateCreate(EmailTemplateBase):
    pass


class EmailTemplateUpdate(BaseModel):
    name: Optional[str] = None
    subject: Optional[str] = None
    html_body: Optional[str] = None
    text_body: Optional[str] = None
    variables: Optional[dict] = None
    status: Optional[str] = Field(default=None, pattern=APPROVAL_STATUS_PATTERN)
    created_by: Optional[uuid.UUID] = None
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    metadata_: Optional[dict] = None


class EmailTemplateRead(EmailTemplateBase):
    model_config = ConfigDict(from_attributes=True)
    template_id: uuid.UUID
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CampaignContentItemBase(BaseModel):
    tenant_id: uuid.UUID
    campaign_id: uuid.UUID
    content_item_id: uuid.UUID
    position: int = 0
    role: Optional[str] = None
    metadata_: Optional[dict] = None


class CampaignContentItemCreate(CampaignContentItemBase):
    pass


class CampaignContentItemUpdate(BaseModel):
    position: Optional[int] = None
    role: Optional[str] = None
    metadata_: Optional[dict] = None


class CampaignContentItemRead(CampaignContentItemBase):
    model_config = ConfigDict(from_attributes=True)
    campaign_content_item_id: uuid.UUID
    created_at: Optional[datetime] = None


class SegmentSyncRunBase(BaseModel):
    tenant_id: uuid.UUID
    segment_id: uuid.UUID
    triggered_by: Optional[uuid.UUID] = None
    status: str = Field(default="Pending", pattern=SYNC_RUN_STATUS_PATTERN)
    dry_run: bool = False
    matched_count: int = 0
    customer_count: int = 0
    lead_count: int = 0
    contact_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    metadata_: Optional[dict] = None


class SegmentSyncRunCreate(SegmentSyncRunBase):
    pass


class SegmentSyncRunUpdate(BaseModel):
    status: Optional[str] = Field(default=None, pattern=SYNC_RUN_STATUS_PATTERN)
    dry_run: Optional[bool] = None
    matched_count: Optional[int] = None
    customer_count: Optional[int] = None
    lead_count: Optional[int] = None
    contact_count: Optional[int] = None
    skipped_count: Optional[int] = None
    error_count: Optional[int] = None
    error_message: Optional[str] = None
    finished_at: Optional[datetime] = None
    metadata_: Optional[dict] = None


class SegmentSyncRunRead(SegmentSyncRunBase):
    model_config = ConfigDict(from_attributes=True)
    sync_run_id: uuid.UUID


class SegmentSyncRouteCounts(BaseModel):
    """Per-routing-bucket counts for one segment -> CRM sync run.

    ``matched`` = resolved segment members; ``customer``/``lead``/``contact`` =
    members routed to each lifecycle bucket; ``skipped`` = lead/contact-routed
    members with no usable identity field; ``error`` = members whose upsert
    raised (a subset already tallied in a route/skipped bucket)."""

    matched: int = 0
    customer: int = 0
    lead: int = 0
    contact: int = 0
    skipped: int = 0
    error: int = 0


class SegmentCrmSyncResponse(BaseModel):
    """Result of ``POST /admin/crm/sync-segment/{segment_id}`` -- the audited
    sync-run summary the marketer sees, plus the per-target write breakdown
    (``detail``) recorded in ``crm_segment_sync_runs.metadata``."""

    sync_run_id: uuid.UUID
    segment_id: uuid.UUID
    tenant_id: uuid.UUID
    status: str
    dry_run: bool
    route_counts: SegmentSyncRouteCounts
    detail: dict = Field(default_factory=dict)
    message: str


DISPATCH_STATUS_PATTERN = "^(Pending|Sent|Failed|Skipped|Suppressed)$"
EMAIL_PROVIDER_PATTERN = "^(mock|smtp)$"


class EmailWebhookEvent(BaseModel):
    """Normalized email provider callback. ``token`` is our tracking
    token (echoed by the ESP from a custom arg set at send time) -- it carries
    the tenant/campaign/profile so the callback correlates without a tenant
    header. ``event`` is mapped to a governed cdp_event_catalog name."""

    token: str = Field(..., description="Our tracking token echoed back by the provider.")
    event: str = Field(..., description="delivered|bounce|complaint|open|click|unsubscribe")
    email: Optional[str] = None
    message_id: Optional[str] = Field(None, description="Provider message/event id, used for dedup when present.")
    bounce_type: Optional[str] = Field(None, description="'hard' | 'soft' -- only hard bounces suppress.")
    timestamp: Optional[str] = None


class CampaignDispatchLogRead(BaseModel):
    """One per-recipient email send ledger row (read-only evidence)."""

    model_config = ConfigDict(from_attributes=True)

    dispatch_id: uuid.UUID
    tenant_id: uuid.UUID
    campaign_id: uuid.UUID
    master_profile_id: uuid.UUID
    template_id: Optional[uuid.UUID] = None
    recipient_email: Optional[str] = None
    status: str
    provider: Optional[str] = None
    provider_message_id: Optional[str] = None
    rendered_subject: Optional[str] = None
    error_message: Optional[str] = None
    run_id: Optional[str] = None
    attempt_count: int = 0
    dispatched_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CampaignActivationResponse(BaseModel):
    """Result of ``POST /admin/campaigns/{id}/activate`` -- the submitted
    campaign_activation Dagster run that will validate + snapshot + hand off to
    the email send."""

    campaign_id: uuid.UUID
    run_id: str
    status: str = "submitted"
    message: str


class EmailProviderConfigUpsert(BaseModel):
    """Writable per-tenant email dispatch config. ``smtp_password`` is
    write-only (accepted here, never returned by the read schema)."""

    name: str = "default"
    provider: str = Field(default="mock", pattern=EMAIL_PROVIDER_PATTERN)
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True
    from_address: Optional[str] = None
    from_name: Optional[str] = None
    is_active: bool = True
    metadata_: Optional[dict] = None


class EmailProviderConfigRead(BaseModel):
    """Per-tenant email dispatch config WITHOUT the secret ``smtp_password``
    (a ``smtp_password_set`` flag signals whether one is stored)."""

    model_config = ConfigDict(from_attributes=True)

    config_id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    provider: str
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_use_tls: bool = True
    from_address: Optional[str] = None
    from_name: Optional[str] = None
    is_active: bool = True
    smtp_password_set: bool = False
    metadata_: Optional[dict] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


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
