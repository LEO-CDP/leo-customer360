## Marketing Campaign Performance Dashboard — Implementation Plan

This implementation plan details how to build the Marketing Campaigns Analytics Dashboard shown in the UI using the provided `customer360` PostgreSQL schema.

---

## 1. System Architecture & UI-to-Schema Mapping

```
+-----------------------------------------------------------------------------------+
|                                 FRONTEND (HTML, handlerbar template, JS)          |
| [ KPI Overview Cards ]   [ Filter Bar ]   [ Performance Table ]   [ Analytics Charts]|
+-----------------------------------------------------------------------------------+
                                         | REST API
                                         v
+-----------------------------------------------------------------------------------+
|                             BACKEND (FastAPI / Python)                            |
|        Endpoints: GET /summary  |  GET /list  |  GET /trends  |  GET /top         |
+-----------------------------------------------------------------------------------+
                                         | SQLAlchemy
                                         v
+-----------------------------------------------------------------------------------+
|                            POSTGRESQL DB (customer360)                            |
|  - crm_campaign                                                                   |
|  - crm_campaign_performance_daily                                                 |
|  - vw_campaign_performance_metrics (Aggregate View)                              |
+-----------------------------------------------------------------------------------+

```

### Data Mapping Summary

| UI Component | Data Source | Primary Fields / Expressions |
| --- | --- | --- |
| **KPI Cards** | `crm_campaign_performance_daily` / `vw_campaign_performance_metrics` | `COUNT(campaign_id)`, `SUM(spend)`, `SUM(impressions)`, `SUM(clicks)`, `SUM(conversions)`, `SUM(revenue_estimated)` |
| **Campaign Table** | `vw_campaign_performance_metrics` | `campaign_code`, `name`, `status`, `channel`, `platform`, `objective`, `total_spend`, `total_impressions`, `total_clicks`, `ctr_percentage`, `total_conversions`, `cpa`, `total_revenue`, `roas` |
| **Spend Trend Chart** | `crm_campaign_performance_daily` | Grouped by `report_date`: `SUM(spend)` over time range |
| **Top Campaigns Charts** | `vw_campaign_performance_metrics` | Top 5 ordered by `total_conversions` DESC and `roas` DESC |

---

## Phase 1: Data Access & Schema Layer (Backend)

### Task 1.1: SQLAlchemy Async Models

**Target File:** `customer360-api/core/models/crm.py`, check class Campaign and update it

**Your task:**

> Create Async SQLAlchemy 2.0 models for the `customer360.crm_campaign` and `customer360.crm_campaign_performance_daily` tables, as well as an immutable model or mapped class for `customer360.vw_campaign_performance_metrics`. Standardize UUID handling and schema naming.


---

### Task 1.2: Pydantic v2 Schemas

**Target File:** `customer360-api/core/schemas/crm.py`

**Your task:**

> Write Pydantic v2 schemas for reading campaign metrics, query parameters (filters, pagination, sorting), aggregate KPI response cards, and time-series trends.

```python
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List
from datetime import date
from decimal import Decimal
from uuid import UUID

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
    """Table row model from customer360.vw_campaign_performance_metrics."""
    model_config = ConfigDict(from_attributes=True)

    campaign_id: UUID
    campaign_code: Optional[str]
    name: str
    status: str
    channel: Optional[str]
    platform: Optional[str]
    objective: Optional[str]
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
    """Aggregate top cards payload."""
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
    campaign_id: UUID
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

```

---

## Phase 2: Analytics & API Layer (FastAPI Service)

### Task 2.1: Campaign Analytics Repository

**Target File:** `customer360-api/core/repositories/campaign_repository.py`

**Your task:**

> Implement an async repository class using SQLAlchemy 2.0 to fetch campaign KPIs, perform filtered pagination on `vw_campaign_performance_metrics`, build daily spend trends, and fetch top campaigns by ROAS and conversions. Apply RLS session variable logic for multi-tenancy.

```python
from uuid import UUID
from typing import Tuple, List, Optional
from decimal import Decimal
from datetime import date
from sqlalchemy import select, func, text, desc, asc, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.campaign import VwCampaignPerformanceMetrics, CRMCampaignPerformanceDaily, CRMCampaign
from app.schemas.campaign import CampaignFilterParams

class CampaignRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID):
        self.session = session
        self.tenant_id = tenant_id

    async def _set_tenant_context(self):
        """Ensure RLS context is set prior to query execution."""
        await self.session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(self.tenant_id)}
        )

    async def get_kpi_summary(self) -> dict:
        """Computes aggregate KPIs displayed on top dashboard cards."""
        await self._set_tenant_context()
        
        stmt = select(
            func.count(VwCampaignPerformanceMetrics.campaign_id).label("total_campaigns"),
            func.coalesce(func.sum(VwCampaignPerformanceMetrics.total_spend), 0).label("total_spend"),
            func.coalesce(func.sum(VwCampaignPerformanceMetrics.total_impressions), 0).label("total_impressions"),
            func.coalesce(func.sum(VwCampaignPerformanceMetrics.total_clicks), 0).label("total_clicks"),
            func.coalesce(func.sum(VwCampaignPerformanceMetrics.total_conversions), 0).label("total_conversions"),
            func.coalesce(func.sum(VwCampaignPerformanceMetrics.total_revenue), 0).label("total_revenue")
        ).where(VwCampaignPerformanceMetrics.tenant_id == self.tenant_id)
        
        res = (await self.session.execute(stmt)).mappings().first()
        
        spend = Decimal(str(res["total_spend"]))
        impressions = int(res["total_impressions"])
        clicks = int(res["total_clicks"])
        conversions = int(res["total_conversions"])
        revenue = Decimal(str(res["total_revenue"]))

        # Formulas for overall KPIs
        # CTR = (Clicks / Impressions) * 100
        overall_ctr = (Decimal(clicks) / Decimal(impressions) * Decimal("100.00")) if impressions > 0 else Decimal("0.00")
        # CVR = (Conversions / Clicks) * 100
        overall_cvr = (Decimal(conversions) / Decimal(clicks) * Decimal("100.00")) if clicks > 0 else Decimal("0.00")
        # ROAS = Revenue / Spend
        overall_roas = (revenue / spend) if spend > 0 else Decimal("0.00")

        return {
            "total_campaigns": res["total_campaigns"],
            "total_spend": spend,
            "total_impressions": impressions,
            "total_clicks": clicks,
            "overall_ctr": round(overall_ctr, 2),
            "total_conversions": conversions,
            "overall_cvr": round(overall_cvr, 2),
            "total_revenue": revenue,
            "overall_roas": round(overall_roas, 2),
        }

    async def get_filtered_campaigns(
        self, filters: CampaignFilterParams
    ) -> Tuple[List[VwCampaignPerformanceMetrics], int]:
        """Fetches filtered list of campaign metrics with dynamic pagination and sorting."""
        await self._set_tenant_context()

        conditions = [VwCampaignPerformanceMetrics.tenant_id == self.tenant_id]

        if filters.search:
            search_pattern = f"%{filters.search}%"
            conditions.append(
                (VwCampaignPerformanceMetrics.name.ilike(search_pattern)) |
                (VwCampaignPerformanceMetrics.campaign_code.ilike(search_pattern))
            )
        if filters.status and filters.status != "All":
            conditions.append(VwCampaignPerformanceMetrics.status == filters.status)
        if filters.channel and filters.channel != "All":
            conditions.append(VwCampaignPerformanceMetrics.channel == filters.channel)
        if filters.platform and filters.platform != "All":
            conditions.append(VwCampaignPerformanceMetrics.platform == filters.platform)
        if filters.objective and filters.objective != "All":
            conditions.append(VwCampaignPerformanceMetrics.objective == filters.objective)

        base_where = and_(*conditions)

        # Total Count
        count_stmt = select(func.count()).select_from(VwCampaignPerformanceMetrics).where(base_where)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        # Dynamic Sorting
        sort_col = getattr(VwCampaignPerformanceMetrics, filters.sort_by, VwCampaignPerformanceMetrics.total_spend)
        order_clause = desc(sort_col) if filters.sort_order.lower() == "desc" else asc(sort_col)

        # Pagination Query
        offset = (filters.page - 1) * filters.page_size
        data_stmt = (
            select(VwCampaignPerformanceMetrics)
            .where(base_where)
            .order_by(order_clause)
            .offset(offset)
            .limit(filters.page_size)
        )
        result = await self.session.execute(data_stmt)
        items = list(result.scalars().all())

        return items, total

    async def get_daily_spend_trend(self, start_date: Optional[date] = None, end_date: Optional[date] = None) -> List[dict]:
        """Returns time series spend data for line chart."""
        await self._set_tenant_context()
        stmt = (
            select(
                CRMCampaignPerformanceDaily.report_date,
                func.sum(CRMCampaignPerformanceDaily.spend).label("spend")
            )
            .where(CRMCampaignPerformanceDaily.tenant_id == self.tenant_id)
            .group_by(CRMCampaignPerformanceDaily.report_date)
            .order_by(CRMCampaignPerformanceDaily.report_date.asc())
        )
        res = await self.session.execute(stmt)
        return [{"report_date": row.report_date, "spend": row.spend} for row in res.all()]

```

---

### Task 2.2: REST Endpoints Route Controller

**Target File:** `customer360-api/core/routers/crm_api.py` check campaigns_router

**Your task:**

> Create FastAPI route handlers for `/api/v1/campaigns/summary`, `/api/v1/campaigns`, and `/api/v1/campaigns/spend-trend` using Depends for async database session injection.

```python
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
import math

from app.db.session import get_db_session
from app.repositories.campaign_repository import CampaignRepository
from app.schemas.campaign import (
    CampaignKPIResponse, PaginatedCampaignResponse, CampaignFilterParams,
    DailySpendTrendItem, CampaignMetricItem
)

router = APIRouter(prefix="/campaigns", tags=["Campaign Analytics"])

# Mock Tenant ID getter (Replace with actual Auth/JWT dependency)
async def get_current_tenant_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000001")

@router.get("/summary", response_model=CampaignKPIResponse)
async def get_campaign_summary(
    db: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """Returns top high-level metric cards for the Campaign overview."""
    repo = CampaignRepository(db, tenant_id)
    return await repo.get_kpi_summary()

@router.get("", response_model=PaginatedCampaignResponse)
async def list_campaign_metrics(
    params: CampaignFilterParams = Depends(),
    db: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """Returns paginated, sorted, and filtered campaign performance rows."""
    repo = CampaignRepository(db, tenant_id)
    items, total = await repo.get_filtered_campaigns(params)
    
    total_pages = math.ceil(total / params.page_size) if params.page_size > 0 else 1

    return PaginatedCampaignResponse(
        items=[CampaignMetricItem.model_validate(i) for i in items],
        total=total,
        page=params.page,
        page_size=params.page_size,
        total_pages=total_pages
    )

@router.get("/spend-trend", response_model=list[DailySpendTrendItem])
async def get_spend_trend(
    db: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """Returns daily time-series performance data for spend visualization."""
    repo = CampaignRepository(db, tenant_id)
    return await repo.get_daily_spend_trend()

```

---



## Phase 3: Frontend dashboard (Handlebars + Chart.js)

FOLDER: `frontend-admin/static/templates/campaign/` + `frontend-admin/static/js/`

### Overview

Follow the exact same patterns used by the existing Segments and Analytics views:
- Handlebars partials in `static/templates/campaign/`
- A dedicated JS view module registered via `C360.router.define()`
- Templates registered in `static/js/templates.js` `SOURCE_PATHS`
- Route tab wired up in `static/js/main.js` `TAB_DEFAULT_PATH`
- API calls via `C360.config.api()` using the tenant-aware helper

---

### Task 3.1: Handlebars Template — KPI Overview Cards

**Target file:** `frontend-admin/static/templates/campaign/campaign-kpi-cards.html`

Create the 9-card KPI overview row (matches `CampaignKPIResponse`):
- Cards: Total Campaigns · Total Spend · Impressions · Clicks · CTR · Conversions · CVR · Revenue · ROAS
- Style: `bg-white border border-slate-200/80 rounded-2xl p-5 shadow-sm hover:shadow-md transition-shadow group`
- Each card has a coloured icon badge (top-right), a bold metric value (`text-2xl font-bold`), and a muted label
- Skeleton loader state: `animate-pulse bg-slate-100 rounded h-7 w-20` for each value while data loads

---

### Task 3.2: Handlebars Template — Filter Bar

**Target file:** `frontend-admin/static/templates/campaign/campaign-filters.html`

Render the filter controls matching `CampaignFilterParams`:
- Text search input (`search`) with debounce (300 ms)
- `<select>` dropdowns for `status`, `channel`, `platform`, `objective` — first option `All`
- Sort-by select (pre-filled with column labels matching `_SORTABLE_COLUMNS`) + asc/desc toggle button
- "Reset filters" button that clears all controls and re-fetches

---

### Task 3.3: Handlebars Template — Campaign Performance Table

**Target file:** `frontend-admin/static/templates/campaign/campaign-table.html`

Data table mapping `CampaignMetricItem` fields to columns — reuse `DataTableView` component pattern from `segments-list.html`:

| Column | Field | Format |
|---|---|---|
| Campaign | `name` + `campaign_code` | identity cell (bold name, muted code tag) |
| Status | `status` | badge (green=Active, yellow=Paused, slate=Draft, indigo=Completed) |
| Channel / Platform | `channel` + `platform` | two-line cell |
| Spend | `total_spend` | `fmt.currency()` right-aligned |
| Impressions | `total_impressions` | `fmt.int()` |
| CTR | `ctr_percentage` | `x.xx%` |
| Conversions | `total_conversions` | `fmt.int()` |
| CPA | `cpa` | `fmt.currency()` |
| ROAS | `roas` | `x.xx×` bold, colour-coded (red < 1, yellow 1–2, green > 2) |

Includes pagination footer (page info + Prev / Next buttons) and loading spinner.

---

### Task 3.4: Handlebars Template — Analytics Charts

**Target file:** `frontend-admin/static/templates/campaign/campaign-charts.html`

Two Chart.js chart containers side-by-side on desktop, stacked on mobile:
- **Left — Campaign Spend Trend** (`<canvas id="campaign-spend-trend-chart">`): line chart fed by `/analytics/spend-trend`
- **Right — Top Campaigns** (`<canvas id="campaign-top-chart">`): horizontal bar chart fed by `/analytics/top`, toggled between Conversions and ROAS via two buttons

Header bar with date-range quick-select buttons (7d / 30d / 90d) that filter the spend trend request.

---

### Task 3.5: JS View Module — `campaign-analytics-view.js`

**Target file:** `frontend-admin/static/js/campaign-analytics-view.js`

Implement `C360.campaignAnalyticsView` following the same IIFE pattern as `C360.analyticsView` in `analytics.js`:

```
mount()           → inject templates, bind filter events, call loadAll()
loadAll()         → parallel $.when( loadKPIs(), loadTable(), loadSpendTrend(), loadTopCampaigns() )
loadKPIs()        → GET /campaigns/analytics/summary  → render KPI cards
loadTable()       → GET /campaigns/analytics?<filters>&<sort>&<page> → render table + pagination
loadSpendTrend()  → GET /campaigns/analytics/spend-trend?start_date=&end_date= → renderChart()
loadTopCampaigns()→ GET /campaigns/analytics/top?limit=5 → renderChart()
renderChart()     → wraps Chart.js, destroys old instance first (same as analytics.js pattern)
bindFilters()     → debounced input + select change → resetPage() + loadTable()
bindPagination()  → Prev/Next click → loadTable() with updated page param
```

Register route: `C360.router.define("/campaigns", { section: "campaign-view", tab: "campaigns", mount: ... })`

---

### Task 3.6: Wire up templates, route, and nav tab

Three small integration changes:

1. **`static/js/templates.js`** — add to `SOURCE_PATHS`:
   ```js
   "campaign-kpi-cards":  "campaign/campaign-kpi-cards",
   "campaign-filters":    "campaign/campaign-filters",
   "campaign-table":      "campaign/campaign-table",
   "campaign-charts":     "campaign/campaign-charts"
   ```
   Add `"campaign-kpi-cards"` to `STANDALONE`, others to `STATIC_HTML`.

2. **`static/js/main.js`** — add `campaigns: "/campaigns"` to `TAB_DEFAULT_PATH`; call `C360.campaignAnalyticsView.bindEvents()` in the `$.ready` block; inject templates into `<section id="campaign-view">` in `loadAll()`.

3. **`base-templates/index.html`** — add `<script src="static/js/campaign-analytics-view.js?v={{ cb }}"></script>` after `analytics.js`; add `<section id="campaign-view" class="hidden"></section>` to the view container; add a Campaigns nav tab button.

---

## Phase 4: Dagster pipeline nhập dữ liệu thực từ Adjust API & GA4 Data API

FOLDER: `backend-system/data_synch/`

### Overview

Replace the placeholder `data_synch_job` with real ops. Follow the same structure as `identity_resolution/dagster_defs.py`:
- Each data source = one Dagster `@op` with `RetryPolicy`
- One `@job` per source, plus a combined `@job` that runs all in sequence
- A `@sensor` polling on a configurable schedule
- All credentials read from env vars (never hardcoded)
- All DB writes use `ON CONFLICT DO UPDATE` (idempotent, safe to re-run)

---

### Task 4.1: Adjust Pull-API Op

**Target file:** `backend-system/data_synch/ops/adjust_pull.py`

Implement `adjust_pull_op` using Adjust Pull API v5:

```
Endpoint: GET https://automate.adjust.com/reports-service/report
                    ?app_token__in=<ADJUST_APP_TOKEN>
                    &date_period=<from>:<to>
                    &dimensions=day,campaign,campaign_id_network
                    &metrics=impressions,clicks,installs,cost
Auth:     Bearer token from env ADJUST_API_TOKEN
Output:   JSON payload (`rows`) → parse rows → upsert into crm_campaign_performance_daily
          (match campaign via metadata->>'utm_campaign' == campaign_code.lower())
Columns mapped:
    day → report_date
  Cost → spend (convert currency if needed)
  Impressions, Clicks, Installs → impressions, clicks, conversions
  Revenue → revenue_estimated
```

Config inputs (Dagster `Config`): `app_id`, `lookback_days` (default 7), `dry_run` flag.

---

### Task 4.2: GA4 Data API Op

**Target file:** `backend-system/data_synch/ops/ga4_pull.py`

Implement `ga4_pull_op` using Google Analytics Data API v1beta (via `google-analytics-data` SDK):

```
Report: runReport on property {GA4_PROPERTY_ID}
Dimensions: date, sessionCampaignName, sessionSource, sessionMedium
Metrics:    sessions, engagedSessions, conversions, totalRevenue, screenPageViews
Date range: last {lookback_days} days
Auth:       Service Account JSON from env GA4_SERVICE_ACCOUNT_JSON (base64)
Output:     Match row to crm_campaign via utm_campaign in metadata JSONB
            Upsert crm_campaign_performance_daily (clicks=sessions, impressions=screenPageViews,
            conversions=conversions, revenue_estimated=totalRevenue)
```

Config inputs: `property_id`, `lookback_days` (default 7), `dry_run` flag.

---

### Task 4.3: DB Upsert Helper

**Target file:** `backend-system/data_synch/ops/db_upsert.py`

Shared helper used by both ops:

```python
def upsert_daily_performance(conn, tenant_id, campaign_id, report_date, metrics: dict) -> None:
    """INSERT ... ON CONFLICT (tenant_id, campaign_id, report_date) DO UPDATE
    Only updates non-null fields (partial merge) so two sources for the same
    day don't overwrite each other's unique columns."""
```

Uses psycopg2 (same as seed scripts) — reads `DB_*` env vars. Logs row count upserted per campaign.

---

### Task 4.4: Campaign Lookup Cache

**Target file:** `backend-system/data_synch/ops/campaign_lookup.py`

```python
def build_campaign_lookup(conn, tenant_id: str) -> dict[str, str]:
    """Returns {campaign_code.lower(): campaign_id} for all campaigns of this tenant.
    Used by both ops to resolve utm_campaign string → UUID foreign key."""
```

Fetches once per job run and passes the dict to both ops via Dagster's op output.

---

### Task 4.5: Dagster Job & Sensor Definitions

**Target file:** `backend-system/data_synch/dagster_defs.py` (replace placeholder)

Define the following:

```python
@job(name="data_synch_adjust_job")   # runs adjust_pull_op only
@job(name="data_synch_ga4_job")          # runs ga4_pull_op only
@job(name="data_synch_job")              # runs both in sequence (replaces placeholder)

@sensor(name="data_synch_daily_sensor",
        job=data_synch_job,
        minimum_interval_seconds=DATA_SYNCH_INTERVAL_SECONDS)
# Fires once per day at midnight VN time (00:00 Asia/Ho_Chi_Minh).
# Uses a cursor to store last_run_date and skips if already ran today.
```

`Definitions(jobs=[...], sensors=[data_synch_daily_sensor])`

---

### Task 4.6: Requirements & Environment

**Target file:** `backend-system/data_synch/requirements.txt`

Add:
```
dagster>=1.9,<2
google-analytics-data>=0.18
requests>=2.31           # Adjust Pull API HTTP client
psycopg2-binary>=2.9
python-dotenv>=1.0
```

**Environment variables** (document in `backend-system/data_synch/.env.example`):
```
ADJUST_API_TOKEN=           # Adjust Reports API bearer token
ADJUST_APP_TOKEN=           # Adjust app token used in report filters
GA4_PROPERTY_ID=            # numeric GA4 property id
GA4_SERVICE_ACCOUNT_JSON=   # base64-encoded service account JSON
DATA_SYNCH_LOOKBACK_DAYS=7
DATA_SYNCH_INTERVAL_SECONDS=86400
DB_HOST / DB_NAME / DB_USER / DB_PASSWORD / DB_PORT / DB_SCHEMA
DEMO_TENANT_ID=11111111-1111-1111-1111-111111111111
```

## Phase 5: AI-powered campaign recommendation

  (dựa trên ROAS / CVR / lifecycle stage từ CIR master profiles)







