# Technical Specification Document: LEO Ad Server Admin APIs

**Target Audience:** Backend Engineering Team

**Framework / Stack:** Python 3.11+, FastAPI, Pydantic v2, Async SQLAlchemy (or SQLModel), PostgreSQL 16+ (`leo_ads` schema), Redis

**Document Version:** 1.0.0

---

## 1. System Architecture & Core Principles

The Backend Admin API exposes secure endpoints to power the Admin Conversational UI and Control Plane. It operates against the `leo_ads` PostgreSQL relational schema, providing administrative oversight for campaign management, creative assets, inventory placements, audience targeting, and real-time performance analytics.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          FastAPI Admin API                              │
├─────────────────────────────────────────────────────────────────────────┤
│  • JWT Authentication & Scope Verification                              │
│  • Tenant Isolation Middleware (tenant_id context)                      │
│  • Pydantic v2 Request / Response Validation                            │
└────────────────────┬──────────────────────────────┬─────────────────────┘
                     │                              │
                     ▼                              ▼
┌───────────────────────────┐         ┌───────────────────────────┐
│     PostgreSQL 16+        │         │        Redis Cache        │
│    (leo_ads schema)       │         │   (Serving Index Sync)    │
│                           │         │                           │
│ • Control Plane Catalog   │         │ • Hot Candidate Lists     │
│ • Materialized Index      │◄────────┤ • Invalidation Pub/Sub    │
│ • Partitioned Events      │         │                           │
└───────────────────────────┘         └───────────────────────────┘

```

### Key Engineering Rules:

1. **Tenant Isolation:** Every incoming request must derive `tenant_id` from the authenticated JWT token or a verified `X-Tenant-Key` context. Raw cross-tenant queries are strictly prohibited.
2. **Soft State Updates:** Modifying operational status (e.g., `active` $\rightarrow$ `paused`) must set `updated_at = NOW()` and trigger index invalidation tasks where applicable.
3. **Cache Synchronization:** Mutating `leo_ads.ad`, `leo_ads.placement`, or `leo_ads.targeting_rule` must trigger an asynchronous cache invalidation event on Redis channel `ads:index:updated`.

---

## 2. API Endpoint Matrix Summary

| Method | Endpoint Path | Database Primary Table(s) | Description |
| --- | --- | --- | --- |
| `GET` | `/api/v1/admin/tenants` | `leo_ads.tenant` | List tenant metadata and configuration settings. |
| `GET` | `/api/v1/admin/advertisers` | `leo_ads.advertiser` | Retrieve advertiser brand profiles. |
| `GET` | `/api/v1/admin/source-accounts` | `leo_ads.source_account` | List external provider/ad network accounts. |
| `GET` | `/api/v1/admin/source-assets` | `leo_ads.source_asset` | List mapped external provider assets. |
| `GET` | `/api/v1/admin/placements` | `leo_ads.placement`, `placement_format` | Retrieve publisher inventory slots. |
| `PATCH` | `/api/v1/admin/placements/{key}/status` | `leo_ads.placement` | Update placement status and sync index. |
| `GET` | `/api/v1/admin/campaigns` | `leo_ads.campaign` | List buying campaigns with budget details. |
| `POST` | `/api/v1/admin/campaigns` | `leo_ads.campaign` | Create a new buying campaign. |
| `PATCH` | `/api/v1/admin/campaigns/{key}/status` | `leo_ads.campaign` | Pause or activate a campaign. |
| `GET` | `/api/v1/admin/creatives` | `leo_ads.creative`, `creative_render` | List ad content and render configurations. |
| `GET` | `/api/v1/admin/ads` | `leo_ads.ad` | List active ad delivery bindings. |
| `PATCH` | `/api/v1/admin/ads/{key}/status` | `leo_ads.ad` | Change ad delivery status & sync Redis index. |
| `GET` | `/api/v1/admin/targeting-rules` | `leo_ads.targeting_rule` | List eligibility criteria per ad. |
| `GET` | `/api/v1/admin/audiences` | `leo_ads.audience` | List precomputed CDP target segments. |
| `GET` | `/api/v1/admin/analytics/performance` | `leo_ads.ad_event` | Aggregate impression, click, conversion metrics. |

---

## 3. Detailed API Endpoint Specifications

### 3.1. Campaign Management

#### `GET /api/v1/admin/campaigns`

* **Description:** Retrieves all campaigns owned by the requesting tenant.
* **Tables Touched:** `leo_ads.campaign` (JOIN `leo_ads.advertiser` optional)
* **Query Parameters:**
* `status` (optional, string): Filter by status (`draft`, `active`, `paused`, `completed`, `archived`).
* `limit` (optional, int, default 50): Pagination limit.
* `offset` (optional, int, default 0): Pagination offset.


* **Response Payload (`200 OK`):**

```json
{
  "total": 3,
  "items": [
    {
      "campaign_id": 1,
      "campaign_key": "coolmate-retargeting",
      "name": "Coolmate Dynamic Retargeting",
      "advertiser_name": "Coolmate",
      "objective": "conversions",
      "buying_model": "CPC",
      "budget_amount": 50000000.0,
      "daily_budget_amount": 3000000.0,
      "currency": "VND",
      "status": "active",
      "starts_at": "2026-06-01T00:00:00Z",
      "ends_at": "2026-08-31T23:59:59Z",
      "created_at": "2026-05-31T10:00:00Z"
    }
  ]
}

```

#### `PATCH /api/v1/admin/campaigns/{campaign_key}/status`

* **Description:** Update campaign operational status.
* **Tables Touched:** `leo_ads.campaign`
* **Path Parameters:** `campaign_key` (string, required)
* **Request Body:**

```json
{
  "status": "paused"
}

```

* **Business Logic:**
1. Validate status value against allowed enum (`draft`, `active`, `paused`, `archived`).
2. Execute SQL update filtering by `tenant_id` and `campaign_key`.
3. If status transitions to `paused` or `archived`, cascade logical status check to child `leo_ads.ad` records in the materialized placement index.


* **Response Payload (`200 OK`):**

```json
{
  "campaign_key": "coolmate-retargeting",
  "previous_status": "active",
  "current_status": "paused",
  "updated_at": "2026-08-16T13:36:37Z"
}

```

---

### 3.2. Publisher Inventory & Placements

#### `GET /api/v1/admin/placements`

* **Description:** Retrieves inventory slots and daily caps.
* **Tables Touched:** `leo_ads.placement`, `leo_ads.placement_format`
* **Response Payload (`200 OK`):**

```json
{
  "total": 2,
  "items": [
    {
      "placement_id": 1,
      "placement_key": "coolmate-banner-300x250",
      "name": "Coolmate Dynamic Retargeting Banner - Desktop",
      "status": "active",
      "min_width_px": 300,
      "min_height_px": 250,
      "responsive": false,
      "daily_impression_cap": 5000,
      "formats": [
        {
          "format_code": "single_banner",
          "width_px": 300,
          "height_px": 250,
          "responsive": false
        }
      ]
    }
  ]
}

```

#### `PATCH /api/v1/admin/placements/{placement_key}/status`

* **Description:** Toggle placement availability.
* **Tables Touched:** `leo_ads.placement`, `leo_ads.placement_ad`
* **Request Body:**

```json
{
  "status": "paused"
}

```

* **Business Logic:**
1. Update `leo_ads.placement.status`.
2. Set `is_active = FALSE` in `leo_ads.placement_ad` where `placement_id` matches.
3. Publish Redis event to channel `ads:index:updated` with payload `{"tenant_id": 1, "placement_key": "coolmate-banner-300x250"}`.


* **Response Payload (`200 OK`):**

```json
{
  "placement_key": "coolmate-banner-300x250",
  "status": "paused",
  "index_invalidated": true
}

```

---

### 3.3. Ad Delivery Objects

#### `PATCH /api/v1/admin/ads/{ad_key}/status`

* **Description:** Controls specific ad delivery eligibility.
* **Tables Touched:** `leo_ads.ad`, `leo_ads.placement_ad`
* **Request Body:**

```json
{
  "status": "active"
}

```

* **Business Logic:**
1. Verify the parent `campaign` and `creative` associated with this `ad` are active.
2. Update `leo_ads.ad.status`.
3. Recompute or toggle `is_active` in `leo_ads.placement_ad`.


* **Response Payload (`200 OK`):**

```json
{
  "ad_key": "coolmate_dynamic_retargeting_01",
  "status": "active",
  "score_weight": 100.0,
  "updated_at": "2026-08-16T13:36:37Z"
}

```

---

### 3.4. Analytics & Telemetry Monitoring

#### `GET /api/v1/admin/analytics/performance`

* **Description:** Aggregates real-time ad events from the partitioned `leo_ads.ad_event` table.
* **Tables Touched:** `leo_ads.ad_event`
* **Query Parameters:**
* `timeframe` (optional, string, default `"24h"`): Supported values `"1h"`, `"24h"`, `"7d"`, `"30d"`.
* `campaign_key` (optional, string): Filter metrics by specific campaign.


* **SQL Logic Template:**

```sql
SELECT 
    COUNT(CASE WHEN event_type = 'impression' THEN 1 END) AS impressions,
    COUNT(CASE WHEN event_type = 'click' THEN 1 END) AS clicks,
    COUNT(CASE WHEN event_type = 'conversion' THEN 1 END) AS conversions,
    COALESCE(SUM(revenue_amount), 0.0) AS total_revenue
FROM leo_ads.ad_event
WHERE tenant_id = :tenant_id
  AND event_time >= NOW() - INTERVAL '24 hours';

```

* **Response Payload (`200 OK`):**

```json
{
  "timeframe": "24h",
  "metrics": {
    "impressions": 72450,
    "clicks": 1820,
    "ctr_percentage": 2.51,
    "conversions": 142,
    "total_revenue_vnd": 35420000.0,
    "currency": "VND"
  }
}

```

---

## 4. Pydantic Request & Response Schemas

To ensure strict type safety and request validation across backend layers, use the following production-ready Pydantic v2 schemas:

```python
# app/schemas/admin_ads.py

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, HttpUrl

# --- ENUM DEFINITIONS ---

class StatusEnum(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"

class BuyingModelEnum(str, Enum):
    CPM = "CPM"
    CPC = "CPC"
    CPA = "CPA"
    VCPM = "vCPM"
    FIXED = "fixed"


# --- CAMPAIGN SCHEMAS ---

class CampaignStatusUpdate(BaseModel):
    status: StatusEnum = Field(..., description="Target status to transition the campaign into.")

class CampaignResponse(BaseModel):
    campaign_id: int
    campaign_key: str
    name: str
    advertiser_name: Optional[str] = None
    objective: Optional[str] = None
    buying_model: Optional[BuyingModelEnum] = None
    budget_amount: Optional[float] = None
    daily_budget_amount: Optional[float] = None
    currency: Optional[str] = "VND"
    status: StatusEnum
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


# --- PLACEMENT SCHEMAS ---

class PlacementFormatSchema(BaseModel):
    format_code: str
    width_px: Optional[int] = None
    height_px: Optional[int] = None
    responsive: bool = False

class PlacementResponse(BaseModel):
    placement_id: int
    placement_key: str
    name: str
    status: StatusEnum
    min_width_px: Optional[int] = None
    min_height_px: Optional[int] = None
    responsive: bool
    daily_impression_cap: Optional[int] = Field(None, description="Extracted from metadata JSONB.")
    formats: List[PlacementFormatSchema] = []

    class Config:
        from_attributes = True

class PlacementStatusUpdate(BaseModel):
    status: StatusEnum


# --- ANALYTICS SCHEMAS ---

class PerformanceMetrics(BaseModel):
    impressions: int = 0
    clicks: int = 0
    ctr_percentage: float = 0.0
    conversions: int = 0
    total_revenue_vnd: float = 0.0
    currency: str = "VND"

class AnalyticsResponse(BaseModel):
    timeframe: str
    metrics: PerformanceMetrics

```

---

## 5. Implementation Guide: FastAPI Router Code

Below is a complete implementation sample for the `/api/v1/admin/campaigns` endpoints using FastAPI and Async SQLAlchemy:

```python
# app/api/v1/routes/admin_campaigns.py

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from typing import List, Optional

from app.database import get_db_session
from app.models.leo_ads import CampaignModel, AdvertiserModel
from app.schemas.admin_ads import CampaignResponse, CampaignStatusUpdate, StatusEnum
from app.dependencies import get_current_tenant_id

router = APIRouter(prefix="/api/v1/admin/campaigns", tags=["Admin Campaigns"])


@router.get("", response_model=List[CampaignResponse], status_code=status.HTTP_200_OK)
async def list_campaigns(
    status_filter: Optional[StatusEnum] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=250),
    offset: int = Query(0, ge=0),
    tenant_id: int = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Fetch all campaigns for the current tenant context with pagination and optional status filtering.
    """
    stmt = (
        select(CampaignModel, AdvertiserModel.name.label("advertiser_name"))
        .outerjoin(AdvertiserModel, CampaignModel.advertiser_id == AdvertiserModel.advertiser_id)
        .where(CampaignModel.tenant_id == tenant_id)
    )

    if status_filter:
        stmt = stmt.where(CampaignModel.status == status_filter.value)

    stmt = stmt.order_by(CampaignModel.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    rows = result.all()

    response_items = []
    for campaign, adv_name in rows:
        item = CampaignResponse(
            campaign_id=campaign.campaign_id,
            campaign_key=campaign.campaign_key,
            name=campaign.name,
            advertiser_name=adv_name,
            objective=campaign.objective,
            buying_model=campaign.buying_model,
            budget_amount=float(campaign.budget_amount) if campaign.budget_amount else None,
            daily_budget_amount=float(campaign.daily_budget_amount) if campaign.daily_budget_amount else None,
            currency=campaign.currency,
            status=campaign.status,
            starts_at=campaign.starts_at,
            ends_at=campaign.ends_at,
            created_at=campaign.created_at,
        )
        response_items.append(item)

    return response_items


@router.patch("/{campaign_key}/status", response_model=Dict[str, Any], status_code=status.HTTP_200_OK)
async def update_campaign_status(
    campaign_key: str,
    payload: CampaignStatusUpdate,
    tenant_id: int = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Update campaign status and trigger database timestamp updates.
    """
    # 1. Fetch current status
    query = select(CampaignModel).where(
        CampaignModel.tenant_id == tenant_id,
        CampaignModel.campaign_key == campaign_key
    )
    result = await db.execute(query)
    campaign = result.scalar_one_or_none()

    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign '{campaign_key}' not found for tenant."
        )

    previous_status = campaign.status

    # 2. Update status
    campaign.status = payload.status.value
    await db.commit()
    await db.refresh(campaign)

    # 3. Return transition response
    return {
        "campaign_key": campaign.campaign_key,
        "previous_status": previous_status,
        "current_status": campaign.status,
        "updated_at": campaign.updated_at
    }

```

---

## 6. Backend Unit & Integration Test Strategy

The backend testing suite is written using `pytest` and FastAPI's `httpx.AsyncClient` targeting an isolated Postgres test database pre-seeded with `sample-data-init.sql` data.

### Sample Test Suite (`tests/test_admin_campaigns.py`)

```python
# tests/test_admin_campaigns.py

import pytest
from httpx import AsyncClient
from fastapi import status

# Pre-seeded test constants from sample-data-init.sql
VALID_TENANT_KEY = "demo"
VALID_CAMPAIGN_KEY = "coolmate-retargeting"
AUTH_HEADERS = {"Authorization": "Bearer mock-valid-admin-jwt", "X-Tenant-Key": VALID_TENANT_KEY}


@pytest.mark.asyncio
async def test_list_campaigns_success(async_client: AsyncClient):
    """
    Test retrieving campaigns returns 200 OK and matches seeded data.
    """
    response = await async_client.get("/api/v1/admin/campaigns", headers=AUTH_HEADERS)
    
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert data[0]["campaign_key"] == VALID_CAMPAIGN_KEY


@pytest.mark.asyncio
async def test_update_campaign_status_success(async_client: AsyncClient):
    """
    Test toggling campaign status from active to paused.
    """
    payload = {"status": "paused"}
    url = f"/api/v1/admin/campaigns/{VALID_CAMPAIGN_KEY}/status"
    
    response = await async_client.patch(url, json=payload, headers=AUTH_HEADERS)
    
    assert response.status_code == status.HTTP_200_OK
    res_data = response.json()
    assert res_data["campaign_key"] == VALID_CAMPAIGN_KEY
    assert res_data["current_status"] == "paused"


@pytest.mark.asyncio
async def test_update_campaign_status_unauthorized_tenant(async_client: AsyncClient):
    """
    Test accessing campaign under a different tenant returns 404 Not Found.
    """
    invalid_headers = {"Authorization": "Bearer mock-valid-admin-jwt", "X-Tenant-Key": "invalid_tenant"}
    payload = {"status": "paused"}
    url = f"/api/v1/admin/campaigns/{VALID_CAMPAIGN_KEY}/status"
    
    response = await async_client.patch(url, json=payload, headers=invalid_headers)
    
    assert response.status_code == status.HTTP_404_NOT_FOUND

```