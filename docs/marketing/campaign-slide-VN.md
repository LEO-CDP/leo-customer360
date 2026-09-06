---
marp: true
theme: default
paginate: true
size: 16:9
style: |
  section {
    font-family: "Inter", "Segoe UI", Roboto, Arial, sans-serif;
    font-size: 24px;
  }

  h1, h2, h3 {
    font-family: "Inter", "Segoe UI", sans-serif;
    font-weight: 700;
  }


  code {
    font-family: "JetBrains Mono", "Consolas", monospace;
  }
---


# Marketing Campaign Performance Dashboard
## Trong nền tảng Customer 360

Đo lường hiệu quả chiến dịch đa kênh theo thời gian thực
(Google Ads · Meta · TikTok · Zalo · Adjust · YouTube · GA4 UTM)

---

## Nội dung

1. Vấn đề: chiến dịch đa kênh không có góc nhìn thống nhất
2. Kiến trúc hệ thống tổng thể
3. Cơ sở dữ liệu: schema & view
4. Adjust và GA4 UTM Tracking
5. Demo dữ liệu mẫu (8 chiến dịch thực tế)
6. Backend: Models → Schemas → Repository → API
7. Unit Tests
8. Các endpoint REST API

---

# 1. Vấn đề

---

## Chiến dịch đa kênh — dữ liệu bị phân mảnh

Một doanh nghiệp chạy cùng lúc nhiều chiến dịch trên nhiều nền tảng, nhưng **dữ liệu hiệu quả nằm rải rác**:

| Nền tảng | Loại dữ liệu | Công cụ đo lường |
|---|---|---|
| **Google Ads** | Paid Search, UAC, Performance Max | GA4 UTM + Google Attribution |
| **Meta Ads** | Paid Social (Facebook/Instagram) | Meta Pixel + Conversions API |
| **TikTok Ads** | Short Video, In-Feed | TikTok Pixel |
| **Zalo Ads** | Zalo Social | Zalo OA Analytics |
| **Adjust** | Mobile App (iOS/Android) | Deep Link + In-App Event |
| **YouTube** | Video Brand Awareness | GA4 UTM (`utm_medium=video`) |

➡️ Không có **một bảng điều khiển thống nhất** → không so sánh được ROAS, CPA, CTR giữa các kênh.

---

## Mục tiêu của Dashboard

- **KPI Overview Cards**: tổng chi tiêu, tổng impressions, clicks, conversions, revenue, ROAS tổng thể
- **Bảng hiệu quả chiến dịch**: lọc theo kênh/nền tảng/mục tiêu, sắp xếp theo bất kỳ chỉ số nào
- **Biểu đồ xu hướng chi tiêu**: time-series theo ngày
- **Top 5 chiến dịch**: theo conversions và ROAS

---

# 2. Kiến trúc hệ thống

---

## Sơ đồ kiến trúc tổng thể

```
┌──────────────────────────────────────────────────────────────┐
│         FRONTEND (HTML + Handlebars template + JS)           │
│  [ KPI Cards ]  [ Filter Bar ]  [ Table ]  [ Charts ]        │
└────────────────────────┬─────────────────────────────────────┘
                         │ REST API (JSON)
                         ▼
┌──────────────────────────────────────────────────────────────┐
│              BACKEND (FastAPI / Python)                       │
│  GET /analytics/summary  |  /analytics  |  /spend-trend      │
│  GET /analytics/top      |  CRUD /campaigns                  │
└────────────────────────┬─────────────────────────────────────┘
                         │ SQLAlchemy ORM
                         ▼
┌──────────────────────────────────────────────────────────────┐
│              POSTGRESQL 16 (customer360 schema)               │
│  crm_campaign  ·  crm_campaign_performance_daily             │
│  vw_campaign_performance_metrics  (Aggregate View)           │
└──────────────────────────────────────────────────────────────┘
```

---

# 3. Cơ sở dữ liệu

---

## Bảng `crm_campaign` — các trường chính

| Nhóm | Trường | Mô tả |
|---|---|---|
| **Định danh** | `campaign_id` (UUID PK), `campaign_code` (VARCHAR 100) | Khóa chính + mã nội bộ duy nhất |
| **Dashboard Dimensions** | `status`, `channel`, `platform`, `objective` | Lọc trên UI |
| **Nội dung** | `name`, `description`, `keywords[]`, `lang` | Mô tả chiến dịch |
| **Thời gian** | `start_date`, `end_date` | Khoảng chạy |
| **Tài chính** | `budget_amount` (NUMERIC 18,2), `currency` (CHAR 3) | Ngân sách |
| **Tracking** | `metadata` JSONB | UTM params, Adjust config |
| **Bảo mật** | `tenant_id`, RLS policy | Cách ly đa tenant |

---

## Bảng `crm_campaign_performance_daily`

Lưu **metrics hàng ngày** theo từng chiến dịch — nền tảng cho time-series chart:

```sql
CREATE TABLE customer360.crm_campaign_performance_daily (
    performance_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID NOT NULL REFERENCES sys_tenant,
    campaign_id       UUID NOT NULL REFERENCES crm_campaign ON DELETE CASCADE,
    report_date       DATE NOT NULL,           -- chiều time dimension
    spend             NUMERIC(18,2),           -- chi tiêu thực tế trong ngày
    impressions       BIGINT,                  -- lượt hiển thị
    clicks            BIGINT,                  -- lượt nhấp
    conversions       BIGINT,                  -- lượt chuyển đổi
    revenue_estimated NUMERIC(18,2),           -- doanh thu ước tính
    UNIQUE (tenant_id, campaign_id, report_date)  -- 1 record/ngày/chiến dịch
);
```

---

## View `vw_campaign_performance_metrics`

**Aggregate view** — tổng hợp toàn bộ lịch sử và tính KPI phái sinh:

```sql
SELECT
    c.tenant_id, c.campaign_id, c.campaign_code, c.name,
    c.status, c.channel, c.platform, c.objective,
    COALESCE(SUM(p.spend), 0)       AS total_spend,
    COALESCE(SUM(p.clicks), 0)      AS total_clicks,
    COALESCE(SUM(p.conversions), 0) AS total_conversions,
    -- CTR = clicks / impressions * 100
    CASE WHEN SUM(p.impressions) > 0
         THEN ROUND(SUM(p.clicks)::NUMERIC / SUM(p.impressions) * 100, 2)
         ELSE 0.00 END AS ctr_percentage,
    -- ROAS = revenue / spend
    CASE WHEN SUM(p.spend) > 0
         THEN ROUND(SUM(p.revenue_estimated) / SUM(p.spend), 2)
         ELSE 0.00 END AS roas
FROM crm_campaign c
LEFT JOIN crm_campaign_performance_daily p ON c.campaign_id = p.campaign_id
GROUP BY c.tenant_id, c.campaign_id, ...
```

---

# 4. Adjust & GA4 UTM Tracking

---

## UTM Tracking — chuẩn đo lường đa kênh

**UTM parameters** (Urchin Tracking Module) được lưu trong `metadata` JSONB của mỗi chiến dịch:

```json
{
  "utm_source":        "google",
  "utm_medium":        "cpc",
  "utm_campaign":      "bank-q4-goog-uac-001",
  "utm_content":       "google-app_install",
  "tracking_platform": "ga4"
}
```

| UTM | Ý nghĩa | Ví dụ |
|---|---|---|
| `utm_source` | Nguồn traffic | `google`, `meta`, `tiktok`, `zalo` |
| `utm_medium` | Loại kênh | `cpc`, `paid_social`, `video`, `push`, `email` |
| `utm_campaign` | Mã chiến dịch | `bank-q4-goog-uac-001` |
| `utm_content` | Biến thể quảng cáo | `google-app_install` |

---

## Adjust — Mobile Attribution

Adjust theo dõi **hành trình cài đặt và in-app event** trên mobile:

```
[Quảng cáo TikTok/Google/Meta]
        ↓ click
[Adjust Deep Link]
        ↓ install detected
[crm_campaign_performance_daily]
  campaign_id = "BANK-CLV-ADJ-005"
    platform    = "Adjust"
    conversions = số lượt install đủ điều kiện
    spend       = chi phí attribution (CPI model)
```

- `platform = "Adjust"` → `tracking_platform = "adjust"` trong metadata
- Metric profile đặc trưng: **CTR cao hơn** (~6%), **CVR cao hơn** (~15%) do retargeting người dùng đã cài

---

## Metric Profile theo nền tảng (dữ liệu mẫu)

| Nền tảng | Impressions/ngày | CTR | CVR | Mô hình |
|---|---|---|---|---|
| **Google UAC** | 8K–40K | 4.5% | 8% | CPC/CPI |
| **Meta** | 15K–60K | 1.8% | 5% | CPM + Conversions API |
| **TikTok** | 20K–80K | 1.2% | 3% | CPM |
| **Zalo** | 5K–25K | 2.5% | 6% | CPC |
| **Adjust** | 3K–15K | **6%** | **15%** | Retargeting (CPI) |
| **YouTube** | 30K–120K | 0.5% | 1.5% | CPV Brand |

---

# 5. Demo dữ liệu mẫu

---

## 8 chiến dịch mẫu — đa kênh, đa nền tảng

| Code | Kênh | Nền tảng | Mục tiêu | Ngân sách | Status |
|---|---|---|---|---|---|
| `BANK-Q4-GOOG-UAC-001` | Paid Search | Google | App Install | 500M VND | Active |
| `RETAIL-MEGA-META-002` | Paid Social | Meta | Conversions | 320M VND | Active |
| `RE-AWARE-TIKTOK-003` | Paid Social | TikTok | Awareness | 180M VND | Active |
| `TRAVEL-Q1-ZALO-004` | Paid Social | Zalo | Leads | 150M VND | Paused |
| `BANK-CLV-ADJ-005` | Push Notification | Adjust | Retention | 200M VND | Active |
| `RETAIL-EMAIL-006` | Email | Google | Conversions | 80M VND | Completed |
| `RE-YT-BRAND-007` | Video | YouTube | Awareness | 250M VND | Active |
| `TRAVEL-PMAX-008` | Paid Search | Google | Conversions | 400M VND | Draft |

---

## Seeding logic — idempotent & deterministic

Mỗi lần chạy `./manage-c360.sh seed-demo` đều cho kết quả **giống hệt**, nhờ:

```python
def stable_rng(key: str) -> random.Random:
    seed = int(hashlib.sha256(key.encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed)

def seed_campaign_performance_daily(cursor, campaign_ids):
    for campaign in CAMPAIGNS:
        rng = stable_rng(f"perf:{campaign_code}")
        impressions = rng.randint(*imp_range)
        clicks      = int(impressions * ctr * rng.uniform(0.8, 1.2))
        conversions = int(clicks * cvr * rng.uniform(0.7, 1.3))
        spend       = round(daily_budget * rng.uniform(0.85, 1.05), 2)
        # INSERT ... ON CONFLICT DO UPDATE → an toàn khi re-run
```

---

# 6. Backend: Models → Schemas → Repository → API

---

## SQLAlchemy Models (Phase 1.1)

**3 models** trong `core/models/crm.py`:

```python
class Campaign(Base):                          # crm_campaign
    campaign_code: Mapped[Optional[str]]       # mới thêm
    status: Mapped[Optional[str]]              # mới thêm
    channel: Mapped[Optional[str]]             # mới thêm
    platform: Mapped[Optional[str]]            # mới thêm
    objective: Mapped[Optional[str]]           # mới thêm
    budget_amount: Mapped[Optional[Decimal]]   # mới thêm
    currency: Mapped[Optional[str]]            # mới thêm

class CRMCampaignPerformanceDaily(Base):       # crm_campaign_performance_daily
    performance_id, report_date, spend,
    impressions, clicks, conversions, revenue_estimated

class VwCampaignPerformanceMetrics(Base):      # view — READ-ONLY
    __table_args__ = {"info": {"is_view": True}}
    total_spend, total_clicks, ctr_percentage, roas, ...
```

---

## Pydantic v2 Schemas (Phase 1.2)

**6 schemas analytics** thêm vào `core/schemas/crm.py`:

| Schema | Mục đích |
|---|---|
| `CampaignFilterParams` | Filter Bar: search, status, channel, platform, objective, sort, page |
| `CampaignMetricItem` | Một hàng trong bảng hiệu quả (từ view) |
| `CampaignKPIResponse` | Payload cho KPI overview cards |
| `DailySpendTrendItem` | Một điểm trong line chart chi tiêu theo ngày |
| `TopCampaignItem` | Một chiến dịch trong Top 5 chart |
| `PaginatedCampaignResponse` | Bọc danh sách + pagination metadata |

---

## Campaign Repository (Phase 2.1)

`core/repositories/campaign_repository.py` — **4 methods**:

```python
class CampaignRepository:
    def _set_tenant_context(self)         # SET app.tenant_id → RLS
    def get_kpi_summary(self) -> dict     # Aggregate KPI cards
    def get_filtered_campaigns(           # Paginated table với filter/sort động
        self, filters: CampaignFilterParams
    ) -> tuple[list, int]
    def get_daily_spend_trend(            # Time-series cho line chart
        self, start_date, end_date
    ) -> list[dict]
    def get_top_campaigns(                # Top N by conversions
        self, limit: int = 5
    ) -> list[dict]
```

> **Bảo mật**: `sort_by` kiểm tra qua `_SORTABLE_COLUMNS` allowlist — ngăn SQL injection qua `getattr` động.

---

## Multi-tenancy & Row Level Security

Mỗi query đều gọi `_set_tenant_context()` trước khi thực thi:

```python
def _set_tenant_context(self) -> None:
    self.session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": str(self.tenant_id)},
    )
```

PostgreSQL RLS policy trên mọi bảng:

```sql
CREATE POLICY tenant_policy ON crm_campaign_performance_daily
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
```

→ Kể cả khi developer quên `WHERE tenant_id = ...`, DB tự chặn dữ liệu tenant khác.

---

# 7. Unit Tests

---

## 16 Test Cases — không cần PostgreSQL thực

Theo pattern `FakeCRUD` đã dùng cho `CdpSegment`:

```python
class FakeCampaignCRUD:
    store: dict[uuid.UUID, SimpleNamespace] = {}

    def create(self, db, obj_in) -> SimpleNamespace:
        obj = SimpleNamespace(campaign_id=uuid.uuid4(), **obj_in)
        self.store[obj.campaign_id] = obj
        return obj
```

| Nhóm | Số test | Nội dung |
|---|---|---|
| **CREATE** | 4 | 201 + ID, thiếu `name` → 422, thiếu `tenant_id` → 422, optional fields |
| **READ** | 4 | get by ID, 404, list all, tenant filter |
| **UPDATE** | 3 | status+budget, 404, UTM metadata |
| **DELETE** | 4 | 204, xóa khỏi list, 404, get sau xóa |
| **COUNT** | 1 | count = số đã tạo |

**Kết quả: 16/16 passed ✅**

---

# 8. REST API Endpoints

---

## Endpoints CRUD — chiến dịch cơ bản

**Prefix**: `/api/v1/campaigns` · Tag: `CRM - Campaigns`

| Method | Path | Mô tả |
|---|---|---|
| `GET` | `/campaigns/` | Danh sách chiến dịch (filter `tenant_id`, `skip`, `limit`) |
| `GET` | `/campaigns/{id}` | Chi tiết một chiến dịch |
| `POST` | `/campaigns/` | Tạo chiến dịch mới |
| `PATCH` | `/campaigns/{id}` | Cập nhật một phần |
| `DELETE` | `/campaigns/{id}` | Xóa chiến dịch |
| `GET` | `/campaigns/count` | Đếm tổng số |

---

## Endpoints Analytics — Dashboard

**Prefix**: `/api/v1/campaigns/analytics` · Tag: `Campaign Analytics`

| Method | Path | Mô tả |
|---|---|---|
| `GET` | `/analytics/summary` | KPI overview cards (spend, ROAS, CTR, CVR…) |
| `GET` | `/analytics` | Bảng hiệu quả có filter/sort/pagination |
| `GET` | `/analytics/spend-trend` | Daily time-series, có `start_date`/`end_date` |
| `GET` | `/analytics/top` | Top N chiến dịch theo conversions, `limit` ≤ 20 |

**Ví dụ request**:
```
GET /api/v1/campaigns/analytics
    ?tenant_id=11111111-1111-1111-1111-111111111111
    &status=Active&platform=Google
    &sort_by=roas&sort_order=desc&page=1&page_size=10
```

---

## Ví dụ Response — KPI Summary

```json
{
  "total_campaigns":   6,
  "total_spend":       "1250000000.00",
  "total_impressions": 4820000,
  "total_clicks":      142500,
  "overall_ctr":       "2.96",
  "total_conversions": 8340,
  "overall_cvr":       "5.85",
  "total_revenue":     "8340000000.00",
  "overall_roas":      "6.67"
}
```

> **Công thức**: CTR = clicks/impressions × 100 · CVR = conversions/clicks × 100 · ROAS = revenue/spend

---

## Tóm tắt triển khai

```
Phase 1 ✅  Data Access & Schema Layer
  crm_campaign              → +7 trường: code, status, channel, platform,
                               objective, budget_amount, currency
  CRMCampaignPerformanceDaily → model mới (daily metrics table)
  VwCampaignPerformanceMetrics → read-only mapped class (aggregate view)
  CampaignBase/Update/Read  → schema cập nhật đầy đủ
  6 analytics schemas       → FilterParams · MetricItem · KPI · Trend · Top · Paginated

Phase 2 ✅  Analytics & API Layer
  CampaignRepository        → 4 methods, RLS context, sort allowlist
  campaign_analytics_router → 4 analytics endpoints
  16 unit tests             → 16/16 passed

Seed ✅
  8 chiến dịch              → đa kênh, UTM metadata, deterministic RNG
  Performance daily         → metrics theo platform profile, idempotent
```

---


## Tiếp theo

- **Phase 3**: Frontend dashboard (Handlebars + Chart.js)
- **Phase 4**: Dagster pipeline nhập dữ liệu thực từ Adjust API & GA4 Data API
- **Phase 5**: AI-powered campaign recommendation
  (dựa trên ROAS / CVR / lifecycle stage từ CIR master profiles)
