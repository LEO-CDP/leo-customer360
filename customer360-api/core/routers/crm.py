"""CRM-style entity routers (Campaign, Lead, Contact, Account, Opportunity,
Industry, LeadSource, CampaignMember), all built via the generic CRUD
router factory since they share a simple single-column UUID primary key.

Also exposes a campaign analytics sub-router under /campaigns/analytics/
for the Marketing Campaign Performance Dashboard (see
docs/PLAN-CAMPAIGNS-DEV.md Phase 2).
"""

import math
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.database import get_db
from core.models.crm import Account, Campaign, CampaignMember, Contact, Industry, Lead, LeadSource, Opportunity
from core.repositories.campaign_repository import CampaignRepository
from core.routers._generic import build_crud_router
from core.schemas.crm import (
    AccountCreate,
    AccountRead,
    AccountUpdate,
    CampaignCreate,
    CampaignFilterParams,
    CampaignKPIResponse,
    CampaignMemberCreate,
    CampaignMemberRead,
    CampaignMemberUpdate,
    CampaignMetricItem,
    CampaignRead,
    CampaignUpdate,
    ContactCreate,
    ContactRead,
    ContactUpdate,
    DailySpendTrendItem,
    IndustryCreate,
    IndustryRead,
    IndustryUpdate,
    LeadCreate,
    LeadRead,
    LeadSourceCreate,
    LeadSourceRead,
    LeadSourceUpdate,
    LeadUpdate,
    OpportunityCreate,
    OpportunityRead,
    OpportunityUpdate,
    PaginatedCampaignResponse,
    TopCampaignItem,
)

campaigns_router = build_crud_router(
    model=Campaign,
    pk_field="campaign_id",
    pk_type=uuid.UUID,
    create_schema=CampaignCreate,
    update_schema=CampaignUpdate,
    read_schema=CampaignRead,
    prefix="/campaigns",
    tags=["CRM - Campaigns"],
)

campaign_members_router = build_crud_router(
    model=CampaignMember,
    pk_field="campaign_member_id",
    pk_type=uuid.UUID,
    create_schema=CampaignMemberCreate,
    update_schema=CampaignMemberUpdate,
    read_schema=CampaignMemberRead,
    prefix="/campaign-members",
    tags=["CRM - Campaigns"],
)

leads_router = build_crud_router(
    model=Lead,
    pk_field="lead_id",
    pk_type=uuid.UUID,
    create_schema=LeadCreate,
    update_schema=LeadUpdate,
    read_schema=LeadRead,
    prefix="/leads",
    tags=["CRM - Leads"],
)

lead_sources_router = build_crud_router(
    model=LeadSource,
    pk_field="lead_source_id",
    pk_type=uuid.UUID,
    create_schema=LeadSourceCreate,
    update_schema=LeadSourceUpdate,
    read_schema=LeadSourceRead,
    prefix="/lead-sources",
    tags=["CRM - Leads"],
)

contacts_router = build_crud_router(
    model=Contact,
    pk_field="contact_id",
    pk_type=uuid.UUID,
    create_schema=ContactCreate,
    update_schema=ContactUpdate,
    read_schema=ContactRead,
    prefix="/contacts",
    tags=["CRM - Accounts"],
)

accounts_router = build_crud_router(
    model=Account,
    pk_field="account_id",
    pk_type=uuid.UUID,
    create_schema=AccountCreate,
    update_schema=AccountUpdate,
    read_schema=AccountRead,
    prefix="/accounts",
    tags=["CRM - Accounts"],
)

opportunities_router = build_crud_router(
    model=Opportunity,
    pk_field="opportunity_id",
    pk_type=uuid.UUID,
    create_schema=OpportunityCreate,
    update_schema=OpportunityUpdate,
    read_schema=OpportunityRead,
    prefix="/opportunities",
    tags=["CRM - Accounts"],
)

industries_router = build_crud_router(
    model=Industry,
    pk_field="industry_id",
    pk_type=uuid.UUID,
    create_schema=IndustryCreate,
    update_schema=IndustryUpdate,
    read_schema=IndustryRead,
    prefix="/industries",
    tags=["CRM - Accounts"],
)


# ---------------------------------------------------------------------------
# Campaign Analytics Router (Phase 2 — Dashboard endpoints)
# Must be defined before all_crm_routers so the list reference resolves.
# ---------------------------------------------------------------------------

campaign_analytics_router = APIRouter(
    prefix="/campaigns/analytics",
    tags=["Campaign Analytics"],
)


@campaign_analytics_router.get("/summary", response_model=CampaignKPIResponse)
def get_campaign_summary(
    tenant_id: uuid.UUID = Query(..., description="Tenant UUID"),
    db: Session = Depends(get_db),
):
    """Aggregate KPI cards: total campaigns, spend, impressions, clicks, conversions, ROAS."""
    repo = CampaignRepository(db, tenant_id)
    return repo.get_kpi_summary()


@campaign_analytics_router.get("", response_model=PaginatedCampaignResponse)
def list_campaign_metrics(
    tenant_id: uuid.UUID = Query(..., description="Tenant UUID"),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    objective: Optional[str] = Query(None),
    sort_by: str = Query("total_spend"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Paginated, filtered, sorted campaign performance table rows."""
    filters = CampaignFilterParams(
        search=search,
        status=status,
        channel=channel,
        platform=platform,
        objective=objective,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    repo = CampaignRepository(db, tenant_id)
    items, total = repo.get_filtered_campaigns(filters)
    return PaginatedCampaignResponse(
        items=[CampaignMetricItem.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if page_size > 0 else 1,
    )


@campaign_analytics_router.get("/spend-trend", response_model=list[DailySpendTrendItem])
def get_spend_trend(
    tenant_id: uuid.UUID = Query(..., description="Tenant UUID"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Daily time-series spend data for the Campaign Spend Trend chart."""
    repo = CampaignRepository(db, tenant_id)
    return repo.get_daily_spend_trend(start_date=start_date, end_date=end_date)


@campaign_analytics_router.get("/top", response_model=list[TopCampaignItem])
def get_top_campaigns(
    tenant_id: uuid.UUID = Query(..., description="Tenant UUID"),
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """Top N campaigns by conversions and ROAS for the analytics charts."""
    repo = CampaignRepository(db, tenant_id)
    return repo.get_top_campaigns(limit=limit)


all_crm_routers = [
    campaign_analytics_router,  # must precede campaigns_router to avoid /{item_id} shadowing /analytics
    campaigns_router,
    campaign_members_router,
    leads_router,
    lead_sources_router,
    contacts_router,
    accounts_router,
    opportunities_router,
    industries_router,
]
