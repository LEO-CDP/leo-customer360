# LEO Ad Server SQL Scripts

Complete database schema and data management documentation for the high-scale ad serving platform. This guide explains the core tables, data contracts, operational logic, and how Dagster admin tasks manage data lifecycle.

---

## Overview

The `leo_ads` schema in PostgreSQL 16+ provides:
- **Multi-tenant isolation** via `tenant_id` on all tables
- **Composable ad structure** separating business logic (Campaign), content (Creative), and delivery (Ad, Placement)
- **Flexible extensibility** via JSONB columns for provider-specific payloads
- **Event streaming** support via partitioned `ad_event` table for high-volume telemetry
- **Precomputed indexes** for low-latency ad serving

**Files in this directory:**
- `db-schema-init.sql` — DDL for all tables, constraints, and indexes
- `sample-data-init.sql` — Demo data seeding (safe to run repeatedly)
- `README.md` — This file (schema documentation + admin workflows)
- `SAMPLE_DATA_EXPLAINATION.md` — Detailed explanation of sample data semantics

---

## Database Architecture

### Schema Layers

```
┌─────────────────────────────────────────────────────────────┐
│  CONTROL PLANE (Admin/Business Logic Management)            │
├─────────────────────────────────────────────────────────────┤
│ • Tenant, Advertiser, SourceAccount, SourceAsset           │
│ • Campaign, Creative, CreativeRender, Creative Items       │
│ • Placement, PlacementFormat                               │
│ • Audience, TargetingRule                                  │
│ → Managed by: Dagster admin tasks, UI forms                │
├─────────────────────────────────────────────────────────────┤
│  SERVING PLANE (Ad Server Queries)                         │
├─────────────────────────────────────────────────────────────┤
│ • Ad, PlacementAd (precomputed index)                       │
│ • UserServingKey (compact, serving-only)                    │
│ → Read-heavy, indexed for <1ms lookups                     │
├─────────────────────────────────────────────────────────────┤
│  TELEMETRY PLANE (Event Stream)                            │
├─────────────────────────────────────────────────────────────┤
│ • AdEvent (partitioned by time)                             │
│ → High-volume write, aggregation queries                   │
│ → Primary sink: Kafka, optional PostgreSQL retention       │
└─────────────────────────────────────────────────────────────┘
```

---

## Core Tables & Data Contracts

### Layer 1: Multi-Tenancy Foundation

#### **Table: `leo_ads.tenant`**

**Purpose:** Isolate all data by customer/organization.

**Fields:**
- `tenant_id` (BIGINT, PK): Unique identifier
- `tenant_key` (VARCHAR, UNIQUE): Business key for APIs (e.g., "demo")
- `name` (VARCHAR): Display name
- `status` (VARCHAR): `'active' | 'paused' | 'archived'`
- `settings` (JSONB): Tenant-wide configuration (country, currency, feature flags)
- `created_at`, `updated_at` (TIMESTAMPTZ): Audit timestamps

**Data Contract:**
- One tenant = one isolated customer/brand
- All other tables MUST reference `tenant_id`
- Settings control tenant-level behavior (country, currency, environment)
- Status controls data access (archived tenants invisible to serving)

**Admin Workflow (Dagster):**
```python
# Create new tenant
INSERT INTO leo_ads.tenant (tenant_key, name, status, settings)
VALUES ('new_customer', 'New Customer Inc', 'active', 
    '{"country": "VN", "currency": "VND"}'::jsonb)

# Pause tenant (disable all serving without deleting)
UPDATE leo_ads.tenant 
SET status = 'paused', updated_at = now() 
WHERE tenant_key = 'new_customer'
```

---

### Layer 2: Advertiser & Account Management

#### **Table: `leo_ads.advertiser`**

**Purpose:** Represent brands/advertisers within a tenant.

**Fields:**
- `advertiser_id` (BIGINT, PK): Internal ID
- `advertiser_key` (VARCHAR, UNIQUE per tenant): Business key
- `tenant_id` (BIGINT, FK): Which tenant owns this advertiser
- `name`, `description`, `title`, `logo_url` (TEXT/VARCHAR): Display info
- `metadata` (JSONB): Category, tier, region, flags
- `created_at`, `updated_at` (TIMESTAMPTZ): Audit

**Data Contract:**
- One advertiser = one brand (Coolmate, ABC Fashion, TechStore)
- Advertiser may own multiple campaigns, creatives, source accounts
- Metadata can store: category, tier (premium/standard), regions, affiliate flags

**Admin Workflow (Dagster):**
```python
# Onboard new advertiser
INSERT INTO leo_ads.advertiser 
  (tenant_id, advertiser_key, name, metadata)
VALUES (1, 'nike-vn', 'Nike Vietnam', 
  '{"category":"sports","tier":"premium","regions":["HCM","HN"]}'::jsonb)

# Update advertiser metadata (e.g., change tier or add region)
UPDATE leo_ads.advertiser 
SET metadata = jsonb_set(metadata, '{tier}', '"enterprise"')
WHERE advertiser_key = 'nike-vn'
```

#### **Table: `leo_ads.source_account`**

**Purpose:** Track external provider accounts (Google Ads, Shopee, Lazada, etc.).

**Fields:**
- `source_account_id` (BIGINT, PK): Internal ID
- `tenant_id` (BIGINT, FK): Tenant ownership
- `advertiser_id` (BIGINT, FK, nullable): Which advertiser (optional)
- `source_type_code` (VARCHAR, FK): Provider type (`'local'`, `'ad_network'`, `'affiliate'`)
- `provider_key` (VARCHAR): Provider name (e.g., `'google_ads'`, `'shopee_affiliate'`)
- `network_key`, `account_key`, `publisher_id`, `api_account_ref` (VARCHAR): Provider-specific IDs
- `status` (VARCHAR): `'active' | 'paused' | 'revoked'`
- `config` (JSONB): Provider-specific config (API endpoints, publishing mode, etc.)
- `secret_ref` (VARCHAR): Reference to secrets manager (not stored in plain text)

**Data Contract:**
- One source account = one provider API account
- Multiple accounts can connect to same provider (multi-account publishers)
- Config contains provider-specific settings (Google Ad Manager mode, Shopee API version)
- Secrets (API keys) stored externally, only reference stored here

**Admin Workflow (Dagster):**
```python
# Add Google Ad Manager account
INSERT INTO leo_ads.source_account 
  (tenant_id, source_type_code, provider_key, network_key, account_key, config, secret_ref)
VALUES (1, 'ad_network', 'google_ads', 'google_ad_manager', 'gam_prod', 
  '{"mode":"js_tag","publisherId":"pub-12345"}'::jsonb, 'secrets/gam-prod-api-key')

# Revoke account (disable without deleting history)
UPDATE leo_ads.source_account 
SET status = 'revoked', updated_at = now() 
WHERE provider_key = 'google_ads' AND account_key = 'gam_prod'
```

#### **Table: `leo_ads.source_asset`**

**Purpose:** Track external assets managed by providers (Google campaigns, Shopee offers, Lazada widgets).

**Fields:**
- `source_asset_id` (BIGINT, PK): Internal ID
- `source_account_id` (BIGINT, FK): Which provider account
- `asset_type` (VARCHAR): Type (`'campaign'`, `'offer'`, `'widget'`, etc.)
- `external_asset_id` (VARCHAR, UNIQUE per account+type): Provider's ID
- `campaign_external_id`, `creative_external_id`, `ad_unit_external_id` (VARCHAR): Related external IDs
- `raw_payload` (JSONB): Full response from provider API (for debugging/audit)
- `status` (VARCHAR): `'active' | 'paused' | 'archived'`

**Data Contract:**
- Maps external provider IDs to local campaign/creative/ad IDs
- Raw payload preserved for audit and provider-specific reconstruction
- Status reflects provider's state (not local override)

**Admin Workflow (Dagster):**
```python
# Store Google campaign sync (asset represents Google campaign)
INSERT INTO leo_ads.source_asset 
  (source_account_id, asset_type, external_asset_id, campaign_external_id, raw_payload)
VALUES (1, 'campaign', 'google-campaign-12345', 'google-campaign-12345', 
  '{"name":"Summer Sale 2026","status":"ENABLED"}'::jsonb)

# Update when provider reports asset archived
UPDATE leo_ads.source_asset 
SET status = 'archived', updated_at = now() 
WHERE external_asset_id = 'google-campaign-12345'
```

---

### Layer 3: Inventory (Placements)

#### **Table: `leo_ads.placement`**

**Purpose:** Define publisher inventory slots where ads appear.

**Fields:**
- `placement_id` (BIGINT, PK): Internal ID
- `placement_key` (VARCHAR, UNIQUE per tenant): Business key (e.g., "homepage_top")
- `tenant_id` (BIGINT, FK): Tenant ownership
- `name` (VARCHAR): Display name
- `status` (VARCHAR): `'active' | 'paused' | 'archived'`
- `min_width_px`, `max_width_px`, `min_height_px`, `max_height_px` (INTEGER, nullable): Size constraints
- `responsive` (BOOLEAN): Whether placement adapts to device width
- `metadata` (JSONB): Position (above-fold, sidebar), daily cap, platform (desktop/mobile/all)
- `created_at`, `updated_at` (TIMESTAMPTZ): Audit

**Data Contract:**
- Placement = stable inventory slot (e.g., homepage top banner always 728x90)
- Dimensions nullable for responsive/native placements
- Responsive=true means can accept variable sizes
- Daily impression cap stored in metadata (operational, not enforced by schema)
- Status controls eligibility (inactive placements excluded from serving)

**Admin Workflow (Dagster):**
```python
# Create placement
INSERT INTO leo_ads.placement 
  (tenant_id, placement_key, name, status, min_width_px, max_width_px, responsive, metadata)
VALUES (1, 'sidebar-300x600', 'Right Sidebar', 'active', 300, 300, false, 
  '{"position":"sidebar","platform":"desktop","dailyImpressionCap":5000}'::jsonb)

# Update metadata (e.g., new daily cap)
UPDATE leo_ads.placement 
SET metadata = jsonb_set(metadata, '{dailyImpressionCap}', '3000') 
WHERE placement_key = 'sidebar-300x600'

# Pause placement (no new ads served)
UPDATE leo_ads.placement 
SET status = 'paused', updated_at = now() 
WHERE placement_key = 'sidebar-300x600'
```

#### **Table: `leo_ads.placement_format`**

**Purpose:** Define supported formats for a placement (optional).

**Fields:**
- `placement_id` (BIGINT, PK/FK): Which placement
- `format_code` (VARCHAR, PK): Format identifier (e.g., "300x250", "responsive", "native")
- `width_px`, `height_px` (INTEGER): Size in pixels
- `width_unit`, `height_unit` (VARCHAR): Unit (`'px'`, `'%'`, `'auto'`)
- `responsive` (BOOLEAN): Whether format is flexible
- `constraints` (JSONB): Format-specific constraints

**Data Contract:**
- One placement can have multiple formats (e.g., homepage_top supports 728x90, 970x90, responsive)
- Not required if placement is simple/fixed
- Constraints can store aspect ratio, min/max sizes, special handling

**Admin Workflow (Dagster):**
```python
# Add format to responsive placement
INSERT INTO leo_ads.placement_format 
  (placement_id, format_code, width_unit, height_unit, responsive, constraints)
SELECT 
  p.placement_id, 
  'responsive', 
  '%', 
  'auto', 
  true, 
  '{"minWidth":"280px","maxWidth":"100%"}'::jsonb
FROM leo_ads.placement p 
WHERE p.placement_key = 'homepage_feed' AND p.tenant_id = 1
```

---

### Layer 4: Business Logic (Campaigns)

#### **Table: `leo_ads.campaign`**

**Purpose:** Define business/buying configuration for ads.

**Fields:**
- `campaign_id` (BIGINT, PK): Internal ID
- `campaign_key` (VARCHAR, UNIQUE per tenant): Business key
- `tenant_id` (BIGINT, FK): Tenant ownership
- `advertiser_id` (BIGINT, FK, nullable): Which advertiser
- `source_account_id` (BIGINT, FK, nullable): Which provider account (if external)
- `name` (VARCHAR): Campaign name
- `objective` (VARCHAR): `'awareness' | 'traffic' | 'conversions' | 'retention' | ...`
- `buying_model` (VARCHAR): `'CPM' | 'CPC' | 'CPA' | 'vCPM' | 'fixed' | ...`
- `budget_amount` (DECIMAL): Total budget
- `currency` (CHAR): ISO 4217 (e.g., "VND")
- `daily_budget_amount` (DECIMAL, nullable): Daily spending cap
- `status` (VARCHAR): `'draft' | 'active' | 'paused' | 'completed' | 'archived'`
- `starts_at`, `ends_at` (TIMESTAMPTZ): Campaign date range
- `metadata` (JSONB): Custom targeting, bid strategy, audience rules
- `created_at`, `updated_at` (TIMESTAMPTZ): Audit

**Data Contract:**
- Campaign separates business logic from content (creative) and delivery (ad/placement)
- One campaign funds multiple creatives and ads
- Budget and date range define campaign lifecycle
- Status controls serving eligibility
- Buying model determines how costs are calculated

**Admin Workflow (Dagster):**
```python
# Create campaign
INSERT INTO leo_ads.campaign 
  (tenant_id, advertiser_id, campaign_key, name, objective, buying_model, 
   budget_amount, currency, daily_budget_amount, status, starts_at, ends_at)
VALUES (1, 1, 'summer-sale-2026', 'Summer Sale 2026', 'conversions', 'CPC', 
  50000000, 'VND', 500000, 'draft', '2026-06-01'::timestamptz, '2026-08-31'::timestamptz)

# Activate campaign (change from draft to active)
UPDATE leo_ads.campaign 
SET status = 'active', updated_at = now() 
WHERE campaign_key = 'summer-sale-2026' AND status = 'draft'

# Pause campaign (stop serving, retain data)
UPDATE leo_ads.campaign 
SET status = 'paused', updated_at = now() 
WHERE campaign_key = 'summer-sale-2026'

# Track budget depletion (Dagster task)
UPDATE leo_ads.campaign 
SET metadata = jsonb_set(metadata, '{spentAmount}', '45000000') 
WHERE campaign_key = 'summer-sale-2026'
```

---

### Layer 5: Content (Creatives)

#### **Table: `leo_ads.creative`**

**Purpose:** Store reusable ad content and assets.

**Fields:**
- `creative_id` (BIGINT, PK): Internal ID
- `creative_key` (VARCHAR, UNIQUE per tenant + version): Business key
- `tenant_id` (BIGINT, FK): Tenant ownership
- `campaign_id` (BIGINT, FK, nullable): Associated campaign (optional)
- `advertiser_id` (BIGINT, FK, nullable): Which advertiser owns content
- `source_asset_id` (BIGINT, FK, nullable): Reference to external asset
- `ad_type` (VARCHAR): `'display' | 'native' | 'video' | 'carousel' | 'dynamic' | ...`
- `format_code` (VARCHAR): Dimensions (`'300x250' | '728x90' | 'responsive' | ...`)
- `render_type_code` (VARCHAR, FK): How to render (`'native_json' | 'js_tag' | 'iframe' | 'html' | 'video' | 'redirect'`)
- `status` (VARCHAR): `'draft' | 'active' | 'paused' | 'archived'`
- `version_no` (INTEGER): Version tracking (v1, v2 for A/B tests)
- `priority` (INTEGER): Ranking within same campaign (lower = less preferred)
- `starts_at`, `ends_at` (TIMESTAMPTZ, nullable): Content availability window
- **Content fields:**
  - `headline`, `subheadline`, `body`, `cta` (TEXT): Common text
  - `image_url`, `video_url`, `logo_url` (TEXT): Asset URLs
- `content_payload` (JSONB): Provider-specific data (Google payload, Shopee product data, etc.)
- `created_at`, `updated_at` (TIMESTAMPTZ): Audit

**Data Contract:**
- Multiple creatives per campaign (A/B variants)
- Creative carries content; Ad determines delivery (placement, frequency)
- Version_no enables A/B testing (same key, different version)
- content_payload stores provider-specific data without schema migration
- Status controls serving eligibility

**Admin Workflow (Dagster):**
```python
# Create creative (v1, control variant)
INSERT INTO leo_ads.creative 
  (tenant_id, campaign_id, creative_key, ad_type, format_code, render_type_code, status,
   version_no, priority, headline, subheadline, body, image_url)
VALUES (1, 1, 'summer-sale-banner', 'display', '728x90', 'html', 'active',
  1, 100, 'Khuyến mãi Hè 2026', 'Giảm giá đến 40%', 'Mua ngay!', 
  'https://example.com/summer.jpg')

# Create A/B variant (v2, test variant with urgency messaging)
INSERT INTO leo_ads.creative 
  (tenant_id, campaign_id, creative_key, ad_type, format_code, render_type_code, status,
   version_no, priority, headline, subheadline, body, image_url)
VALUES (1, 1, 'summer-sale-banner', 'display', '728x90', 'html', 'active',
  2, 95, 'Chỉ còn lại 2 ngày!', 'Giảm giá đến 40%', '🔥 Mua ngay!', 
  'https://example.com/summer-urgency.jpg')

# Update creative metadata (e.g., track performance)
UPDATE leo_ads.creative 
SET metadata = jsonb_set(metadata, '{variant}', '"urgency"'::jsonb) 
WHERE creative_key = 'summer-sale-banner' AND version_no = 2

# Pause underperforming variant
UPDATE leo_ads.creative 
SET status = 'paused', updated_at = now() 
WHERE creative_key = 'summer-sale-banner' AND version_no = 2
```

#### **Table: `leo_ads.creative_item`**

**Purpose:** Store carousel items, product carousel, recommendation cards.

**Fields:**
- `creative_item_id` (BIGINT, PK): Internal ID
- `creative_id` (BIGINT, FK): Which creative owns this item
- `external_item_id` (VARCHAR, UNIQUE per creative): Provider's item ID
- `item_type` (VARCHAR): `'product' | 'article' | 'video' | 'custom'`
- `item_name`, `subtitle` (VARCHAR/TEXT): Display text
- `price_amount`, `original_price_amount` (DECIMAL): Pricing
- `currency` (CHAR): ISO 4217
- `discount_text` (VARCHAR): Display text (e.g., "-20%")
- `image_url` (TEXT): Item image
- `destination_url` (TEXT): Click destination
- `highlight_text` (VARCHAR): Special badge (e.g., "Trending", "Low Stock")
- `sort_order` (SMALLINT): Position in carousel (0=first)
- `item_payload` (JSONB): Provider-specific data (stock level, rating, etc.)

**Data Contract:**
- Carousel = creative with multiple items
- Each item references external product/article ID
- Items ordered by sort_order
- Pricing and discount text for e-commerce use cases

**Admin Workflow (Dagster):**
```python
-- Add product carousel items (Coolmate product carousel)
INSERT INTO leo_ads.creative_item 
  (creative_id, external_item_id, item_type, item_name, price_amount, 
   original_price_amount, currency, discount_text, image_url, destination_url, 
   highlight_text, sort_order, item_payload)
SELECT 
  c.creative_id,
  x.external_item_id,
  'product',
  x.item_name,
  x.price_amount,
  x.original_price_amount,
  'VND',
  x.discount_text,
  x.image_url,
  x.destination_url,
  x.highlight,
  x.sort_order,
  x.item_payload::jsonb
FROM leo_ads.creative c
CROSS JOIN (
  VALUES
    ('p1', 'Áo sơ mi nam Coolmate Classic', 399000, 499000, '-20%', 
     'https://example.com/p1.jpg', 'https://example.com/p1', 'Bán chạy', 0,
     '{"rating":4.8,"reviews":1250,"stock":45}'),
    ('p2', 'Áo thun nam Essential', 249000, 275000, '-9%', 
     'https://example.com/p2.jpg', 'https://example.com/p2', 'Top Trending', 1,
     '{"rating":4.6,"reviews":890,"stock":112}')
) AS x(external_item_id, item_name, price_amount, original_price_amount, 
       discount_text, image_url, destination_url, highlight, sort_order, item_payload)
WHERE c.creative_key = 'product-carousel-01' AND c.tenant_id = 1
```

#### **Table: `leo_ads.destination`**

**Purpose:** Store click destinations for creatives.

**Fields:**
- `destination_id` (BIGINT, PK): Internal ID
- `creative_id` (BIGINT, FK): Which creative
- `destination_type_code` (VARCHAR, FK): Type (`'url' | 'product' | 'affiliate_url' | 'app_deep_link' | 'custom'`)
- `url`, `final_url` (TEXT, at least one): Where user goes on click
- `metadata` (JSONB): Tracking params, conversion goals, etc.

**Data Contract:**
- Each creative can have one primary destination per type
- URL may be short-link with tracking; final_url is real destination
- Metadata stores UTM params, conversion event type, etc.

#### **Table: `leo_ads.tracking_endpoint`**

**Purpose:** Store impression, click, conversion tracking URLs.

**Fields:**
- `tracking_endpoint_id` (BIGINT, PK): Internal ID
- `creative_id` (BIGINT, FK): Which creative to track
- `event_type` (VARCHAR): `'impression' | 'click' | 'viewable_impression' | 'conversion' | 'video_start' | 'video_complete'`
- `endpoint_url` (TEXT): Where to send tracking request
- `method` (VARCHAR): `'GET' | 'POST' | 'PIXEL'`
- `extra` (JSONB): Custom tracking params

**Data Contract:**
- One endpoint per creative per event type
- Serves as source of truth for tracking requests
- Can be third-party (Google Analytics, Facebook Pixel) or internal

---

### Layer 6: Ad Delivery

#### **Table: `leo_ads.ad`**

**Purpose:** Represent an ad serving configuration (links Campaign, Creative, Placement).

**Fields:**
- `ad_id` (BIGINT, PK): Internal ID (DO NOT expose to users)
- `ad_key` (VARCHAR, UNIQUE per tenant): Business key (expose this to APIs)
- `tenant_id` (BIGINT, FK): Tenant ownership
- `campaign_id` (BIGINT, FK, nullable): Which campaign (optional)
- `creative_id` (BIGINT, FK): Which creative (required, restricted delete)
- `placement_id` (BIGINT, FK): Which placement (required, restricted delete)
- `status` (VARCHAR): `'draft' | 'active' | 'paused' | 'archived'`
- `score_weight` (REAL): Ranking preference (100 = highest priority, 1 = lowest)
- `frequency_cap` (INTEGER, nullable): Max impressions per user per period
- `metadata` (JSONB): Ad-specific flags, A/B test variant info, etc.
- `created_at`, `updated_at` (TIMESTAMPTZ): Audit

**Data Contract:**
- Ad = minimal serving configuration (no duplication of campaign/creative/placement data)
- Foreign keys to creative/placement are restricted (cannot delete if ad uses them)
- Foreign key to campaign is nullable (ads can exist without campaigns)
- Score_weight determines ranking in candidate selection
- Status controls serving eligibility

**Admin Workflow (Dagster):**
```python
# Create ad (links campaign + creative + placement)
INSERT INTO leo_ads.ad 
  (tenant_id, campaign_id, creative_id, placement_id, ad_key, status, score_weight, frequency_cap)
VALUES (1, 1, 1, 1, 'ad-coolmate-banner-01', 'active', 100.0, 5)

# Update score weight (change ranking priority)
UPDATE leo_ads.ad 
SET score_weight = 95.0, updated_at = now() 
WHERE ad_key = 'ad-coolmate-banner-01'

# Pause ad (stop serving)
UPDATE leo_ads.ad 
SET status = 'paused', updated_at = now() 
WHERE ad_key = 'ad-coolmate-banner-01'

# Archive ad (logical delete, preserve history for reporting)
UPDATE leo_ads.ad 
SET status = 'archived', updated_at = now() 
WHERE ad_key = 'ad-coolmate-banner-01'
```

#### **Table: `leo_ads.placement_ad`**

**Purpose:** Precomputed index of which ads can serve on which placements (for caching).

**Fields:**
- `placement_id` (BIGINT, PK/FK): Placement
- `ad_id` (BIGINT, PK/FK): Ad candidate
- `rank_score` (REAL): Ranking score (for sorting)
- `valid_from`, `valid_to` (TIMESTAMPTZ, nullable): When this pairing is eligible
- `is_active` (BOOLEAN): Whether to include in serving

**Data Contract:**
- Materialized view of eligible (placement, ad) pairs
- Enables pre-filtering before ranking/selection
- Can be synced to Redis for ultra-low latency
- Not auto-maintained by schema; populated by Dagster task

**Admin Workflow (Dagster):**
```python
-- Compute/refresh placement_ad index (materialized view)
-- This task runs on schedule to rebuild the index
DELETE FROM leo_ads.placement_ad;

INSERT INTO leo_ads.placement_ad (placement_id, ad_id, rank_score, is_active)
SELECT 
  a.placement_id,
  a.ad_id,
  a.score_weight,
  (a.status = 'active' AND c.status = 'active' AND p.status = 'active')
FROM leo_ads.ad a
JOIN leo_ads.creative c ON c.creative_id = a.creative_id
JOIN leo_ads.placement p ON p.placement_id = a.placement_id
WHERE a.tenant_id = 1;

-- Sync to Redis (from Dagster task)
-- SELECT placement_id, array_agg(ad_id ORDER BY rank_score DESC) as candidate_ads
-- FROM leo_ads.placement_ad WHERE is_active = true GROUP BY placement_id
-- THEN REDIS SET placement:{placement_id}:candidates {candidate_ads}
```

---

### Layer 7: Targeting & Audience

#### **Table: `leo_ads.targeting_rule`**

**Purpose:** Define eligibility rules for ads (who can see them).

**Fields:**
- `targeting_rule_id` (BIGINT, PK): Internal ID
- `ad_id` (BIGINT, FK): Which ad this rule applies to
- `tenant_id` (BIGINT, FK): Tenant ownership
- `audience_key` (VARCHAR): Reference to precomputed audience (optional)
- `countries` (TEXT[]): Allowed countries (e.g., `["VN", "TH"]`)
- `regions` (TEXT[]): Allowed regions (e.g., `["HCM", "HN", "Hanoi"]`)
- `device_types` (TEXT[]): Allowed devices (e.g., `["mobile", "desktop"]`)
- `os_types` (TEXT[]): Allowed OS (e.g., `["iOS", "Android"]`)
- `browser_types` (TEXT[]): Allowed browsers
- `languages` (TEXT[]): Allowed languages
- `min_age`, `max_age` (SMALLINT, nullable): Age range
- `gender_codes` (TEXT[], nullable): Gender targeting
- `context_keywords` (TEXT[]): Context keywords (article category, page type)
- `custom_predicates` (JSONB): Custom conditions (e.g., `{"retargeting":true,"ltv":"high"}`)
- `exclude_predicates` (JSONB): Negative targeting
- `priority` (INTEGER): Rule priority (higher = checked first)

**Data Contract:**
- Ad can have multiple targeting rules (OR logic between rules)
- Fields within a rule are AND logic (all must match)
- custom_predicates enable arbitrary conditions (evaluated at serving time)
- Audience reference is optional; can rely on direct predicates or combine both

**Admin Workflow (Dagster):**
```python
-- Create targeting rule: High-value retargeting
INSERT INTO leo_ads.targeting_rule 
  (ad_id, tenant_id, countries, device_types, languages, 
   context_keywords, custom_predicates, priority)
SELECT 
  a.ad_id,
  a.tenant_id,
  '["VN"]'::text[],
  '["mobile","desktop"]'::text[],
  '["vi"]'::text[],
  '["fashion","menswear"]'::text[],
  '{"retargeting":true,"recentProductViewDays":30,"minTimeOnSite":30}'::jsonb,
  100
FROM leo_ads.ad a
WHERE a.ad_key = 'ad-coolmate-banner-01'

-- Create targeting rule: Mobile urgency (specific times)
INSERT INTO leo_ads.targeting_rule 
  (ad_id, tenant_id, device_types, custom_predicates, priority)
SELECT 
  a.ad_id,
  a.tenant_id,
  '["mobile"]'::text[],
  '{"cartValue":">100000","cartAbandonment":true,"daysSinceLast":"7-30","timeOfDay":"08:00-12:00,18:00-23:00"}'::jsonb,
  95
FROM leo_ads.ad a
WHERE a.ad_key = 'ad-coolmate-banner-01'
```

#### **Table: `leo_ads.audience`**

**Purpose:** Precomputed audience definitions (member segments).

**Fields:**
- `audience_id` (BIGINT, PK): Internal ID
- `audience_key` (VARCHAR, UNIQUE per tenant): Business key
- `tenant_id` (BIGINT, FK): Tenant ownership
- `name` (VARCHAR): Display name
- `provider` (VARCHAR, nullable): Where audience comes from (CDP, Google, Shopee)
- `external_audience_id` (VARCHAR, nullable): Provider's ID
- `membership_version` (BIGINT): Version for tracking updates
- `member_count_estimate` (BIGINT, nullable): Size estimate (not exact)
- `definition` (JSONB): How audience is defined (event, metrics, behavior)
- `status` (VARCHAR): `'active' | 'paused' | 'archived'`

**Data Contract:**
- Audience = named segment (e.g., "High-Value Retargeting", "Mobile Users")
- Definition stores: event type, lookback days, predicates (e.g., `ltv > 3M VND`)
- DO NOT store 40M user membership rows here; use external audience system
- member_count_estimate is advisory only

**Admin Workflow (Dagster):**
```python
-- Define audience segment (High-value LTV)
INSERT INTO leo_ads.audience 
  (tenant_id, audience_key, name, provider, membership_version, member_count_estimate, definition)
VALUES (1, 'high_value_ltv', 'High-Value Customers (LTV > 3M)', 'internal',
  1, 35000, 
  '{"event":"ltv_metric","metricType":"lifetime_value","minValue":3000000,"lookbackDays":"all","definition":"customers_with_ltv_gt_3m"}'::jsonb)

-- Define mobile segment
INSERT INTO leo_ads.audience 
  (tenant_id, audience_key, name, provider, membership_version, member_count_estimate, definition)
VALUES (1, 'mobile_users_vn', 'Mobile Users (Vietnam)', 'internal',
  1, 285000, 
  '{"event":"device_context","device":"mobile","country":"VN","lookbackDays":7}'::jsonb)

-- Sync audience membership from CDP (Dagster task)
-- SELECT user_id FROM cdp.segments WHERE segment_key = 'high_value_ltv'
-- THEN UPDATE leo_ads.audience SET membership_version = (version + 1)
-- Membership management typically external via feature store
```

#### **Table: `leo_ads.ad_audience`**

**Purpose:** Link ads to audiences (include/exclude).

**Fields:**
- `ad_id` (BIGINT, PK/FK): Which ad
- `audience_id` (BIGINT, PK/FK): Which audience
- `relation_type` (VARCHAR, PK): `'include'` (must be in audience) or `'exclude'` (must not be)

**Data Contract:**
- Many-to-many: ad can use multiple audiences
- Include rules = who can see ad
- Exclude rules = who cannot see ad (negative targeting)
- Both can apply (include A but exclude B)

**Admin Workflow (Dagster):**
```python
-- Include high-value audience in ad
INSERT INTO leo_ads.ad_audience (ad_id, audience_id, relation_type)
SELECT a.ad_id, aud.audience_id, 'include'
FROM leo_ads.ad a
JOIN leo_ads.audience aud ON aud.audience_key = 'high_value_ltv'
WHERE a.ad_key = 'ad-coolmate-banner-01' AND a.tenant_id = 1

-- Exclude new users (only show to existing customers)
INSERT INTO leo_ads.ad_audience (ad_id, audience_id, relation_type)
SELECT a.ad_id, aud.audience_id, 'exclude'
FROM leo_ads.ad a
JOIN leo_ads.audience aud ON aud.audience_key = 'new_users'
WHERE a.ad_key = 'ad-coolmate-banner-01' AND a.tenant_id = 1
```

---

### Layer 8: User Serving (Compact Profile)

#### **Table: `leo_ads.user_serving_key`**

**Purpose:** Compact serving-only user profile (not full CDP).

**Fields:**
- `user_key_hash` (BYTEA, PK): Hash of user ID (for privacy)
- `tenant_id` (BIGINT, FK): Which tenant
- `profile_ref` (VARCHAR): Reference to CDP profile
- `first_seen_at`, `last_seen_at` (TIMESTAMPTZ): Activity tracking
- `attributes` (JSONB): Serving-relevant attributes (segment, device, ltv, purchase count, interests)
- `updated_at` (TIMESTAMPTZ): Last sync time

**Data Contract:**
- Compact, serving-optimized user data (not the full CDP profile)
- Hash-based to avoid storing PII
- Attributes stored in JSONB: `{segment, ltv, device, interests, purchase_count, ...}`
- Synced from CDP on schedule

**Admin Workflow (Dagster):**
```python
-- Sync user data from CDP (Dagster sync task)
-- This runs periodically to refresh serving profiles
DELETE FROM leo_ads.user_serving_key WHERE tenant_id = 1;

INSERT INTO leo_ads.user_serving_key 
  (user_key_hash, tenant_id, profile_ref, first_seen_at, last_seen_at, attributes)
SELECT 
  digest(up.user_id, 'sha256'),
  1,
  up.cdp_profile_id,
  up.first_visit_at,
  up.last_activity_at,
  jsonb_build_object(
    'segment', up.segment_name,
    'device', up.preferred_device,
    'ltv', up.lifetime_value_vnd,
    'purchase_count', up.total_purchases,
    'interests', up.interests::jsonb
  )
FROM cdp.user_profiles up
WHERE up.tenant_id = 1 AND up.active = true
```

---

### Layer 9: Telemetry (Event Stream)

#### **Table: `leo_ads.ad_event`**

**Purpose:** High-volume event stream (impressions, clicks, conversions).

**Fields:**
- `event_id` (UUID, PK): Unique event identifier
- `event_time` (TIMESTAMPTZ, PK): When event occurred (partition key)
- `tenant_id` (BIGINT, FK): Event owner
- `event_type` (VARCHAR): `'impression' | 'click' | 'viewable_impression' | 'conversion' | ...`
- `ad_id`, `campaign_id`, `creative_id`, `placement_id` (BIGINT): Context IDs
- `user_key_hash` (BYTEA): User identifier (hashed for privacy)
- `request_id`, `session_id` (UUID): Trace IDs for multi-touch attribution
- `device_type`, `country_code` (VARCHAR): Context
- `page_url` (TEXT): Where event occurred
- `revenue_amount` (DECIMAL, nullable): Transaction value (for conversions)
- `currency` (CHAR): ISO 4217
- `payload` (JSONB): Custom event data
- **Partitioned by:** `event_time` (monthly partitions)

**Data Contract:**
- High-volume write table (millions of events per day)
- Partitioned by time for efficient retention and querying
- Primary sink: Kafka for streaming, PostgreSQL for recent operational window
- Not queried directly at serving time (only aggregations)

**Admin Workflow (Dagster):**
```python
-- This table is write-only at serving time (via Kafka producer in ad-api)
-- Dagster tasks consume Kafka and write to PostgreSQL:

INSERT INTO leo_ads.ad_event 
  (event_id, event_time, tenant_id, event_type, ad_id, campaign_id, 
   creative_id, placement_id, user_key_hash, request_id, session_id, 
   device_type, country_code, page_url, revenue_amount, currency, payload)
SELECT 
  kafka_event->>'event_id'::uuid,
  (kafka_event->>'event_time')::timestamptz,
  (kafka_event->>'tenant_id')::bigint,
  kafka_event->>'event_type',
  (kafka_event->>'ad_id')::bigint,
  (kafka_event->>'campaign_id')::bigint,
  (kafka_event->>'creative_id')::bigint,
  (kafka_event->>'placement_id')::bigint,
  digest(kafka_event->>'user_id', 'sha256'),
  (kafka_event->>'request_id')::uuid,
  (kafka_event->>'session_id')::uuid,
  kafka_event->>'device_type',
  kafka_event->>'country_code',
  kafka_event->>'page_url',
  (kafka_event->>'revenue_amount')::decimal,
  kafka_event->>'currency',
  (kafka_event->'custom')::jsonb
FROM kafka_topic_ads_events
WHERE processed = false

-- Aggregation queries (run by analytics/reporting task)
SELECT 
  DATE_TRUNC('day', event_time) as date,
  ad_id,
  event_type,
  COUNT(*) as count,
  COUNT(DISTINCT user_key_hash) as unique_users,
  SUM(revenue_amount) as total_revenue
FROM leo_ads.ad_event
WHERE event_time >= CURRENT_DATE - INTERVAL '30 days'
  AND tenant_id = 1
GROUP BY 1, 2, 3
```

---

## Data Flow: Admin Operations via Dagster

### Workflow 1: Onboard New Campaign

```
1. Admin creates campaign
   INSERT leo_ads.campaign (tenant_id, advertiser_id, name, ...)
   
2. Admin creates creatives
   INSERT leo_ads.creative (campaign_id, ...) 
   for each variant (A/B test)
   
3. Admin adds creative items (carousel products)
   INSERT leo_ads.creative_item (creative_id, ...)
   
4. Admin configures destinations & tracking
   INSERT leo_ads.destination (creative_id, ...)
   INSERT leo_ads.tracking_endpoint (creative_id, ...)
   
5. Admin creates ads (links campaign + creative + placement)
   INSERT leo_ads.ad (campaign_id, creative_id, placement_id, ...)
   
6. Admin configures targeting rules
   INSERT leo_ads.targeting_rule (ad_id, ...)
   INSERT leo_ads.ad_audience (ad_id, audience_id, ...)
   
7. Admin activates campaign
   UPDATE leo_ads.campaign SET status = 'active' WHERE campaign_id = ?
   
8. Dagster recomputes placement_ad index
   DELETE FROM leo_ads.placement_ad
   INSERT INTO leo_ads.placement_ad (SELECT ... from leo_ads.ad)
   REDIS SYNC: Publish new index to Redis cache
```

### Workflow 2: A/B Test & Optimize

```
1. Admin creates creative variant (v2)
   INSERT leo_ads.creative 
     (campaign_id, creative_key, version_no=2, priority=95, ...)
   
2. Admin creates ad for new variant
   INSERT leo_ads.ad (creative_id=v2, score_weight=95, ...)
   
3. Campaign runs; Dagster collects events
   - Impressions logged to ad_event table
   - Events aggregated to ad_performance
   
4. Admin reviews performance (report)
   SELECT 
     c.creative_key,
     c.version_no,
     COUNT(*) as impressions,
     SUM(CASE WHEN event_type='click' THEN 1 ELSE 0 END) as clicks,
     100.0 * SUM(CASE WHEN event_type='click' THEN 1 ELSE 0 END) / COUNT(*) as ctr_pct
   FROM leo_ads.ad_event ae
   JOIN leo_ads.creative c ON c.creative_id = ae.creative_id
   GROUP BY 1, 2
   
5. Admin promotes winning variant
   UPDATE leo_ads.creative 
   SET score_weight = 100, priority = 100 
   WHERE creative_key = 'summer-sale-banner' AND version_no = 2
   
6. Admin pauses losing variant
   UPDATE leo_ads.creative 
   SET status = 'paused' 
   WHERE creative_key = 'summer-sale-banner' AND version_no = 1
   
7. Dagster recomputes index
   (PLACEMENT_AD refresh)
```

### Workflow 3: Audience Sync & Targeting

```
1. CDP defines new audience
   (External system: CDP/Feature Store)
   
2. Dagster syncs audience to ads-server
   SELECT user_id FROM cdp.segments WHERE segment_key = 'high_value'
   → Update leo_ads.audience (membership_version++)
   → Update leo_ads.user_serving_key with segment attribute
   
3. Admin uses audience in ad targeting
   INSERT leo_ads.ad_audience (ad_id, audience_id, 'include')
   
4. At serving time: AD API checks
   SELECT * FROM leo_ads.ad_audience WHERE ad_id = ? AND relation_type = 'include'
   → Verify user is in included audiences
   
5. Dagster monitors audience freshness
   SELECT MAX(updated_at) FROM leo_ads.audience WHERE tenant_id = ?
   ALERT if > 1 day old (sync broken)
```

### Workflow 4: Budget Management & Pacing

```
1. Admin sets campaign budget
   INSERT leo_ads.campaign (budget_amount=50M VND, daily_budget_amount=500K)
   
2. At runtime: AD API tracks spend
   (Kafka stream or direct DB write)
   
3. Dagster budget reconciliation task (hourly)
   SELECT 
     SUM(revenue_amount) as daily_spend
   FROM leo_ads.ad_event
   WHERE DATE(event_time) = CURRENT_DATE
     AND campaign_id = ?
   
   IF daily_spend >= daily_budget_amount:
     UPDATE leo_ads.campaign SET status = 'paused'
     ALERT admin
     
4. Dagster resets daily budget next day
   (Scheduled task at 00:00 UTC)
```

### Workflow 5: Provider Account Sync

```
1. Admin links external provider (Google Ads, Shopee, etc.)
   INSERT leo_ads.source_account (provider_key='google_ads', config=..., secret_ref=...)
   
2. Dagster sync task polls provider API (periodic)
   For each source_account:
     - Fetch campaigns, creatives, performance from provider
     - Store as leo_ads.source_asset (asset_type='campaign', raw_payload=full_api_response)
     - Map external IDs to local campaign/creative/ad IDs
     
3. Dagster creates local objects
   For each source_asset with type='campaign':
     INSERT leo_ads.campaign 
       (source_account_id, campaign_key=external_id, ...)
       
4. Admin reviews synced data (reconciliation report)
   SELECT 
     sa.external_asset_id,
     lc.campaign_key,
     lc.status,
     sa.raw_payload->>'status' as provider_status
   FROM leo_ads.source_asset sa
   LEFT JOIN leo_ads.campaign lc ON lc.campaign_key = sa.external_asset_id
   WHERE sa.asset_type = 'campaign'
   
5. Dagster keeps data in sync
   (Continuous polling and update)
```

---

## Dagster Task Templates

### Task: Refresh Placement Ad Index

```python
@asset
def placement_ad_index(postgres_io: PostgresIO, tenant_id: int) -> None:
    """Recompute which ads can serve on which placements."""
    
    sql = """
    DELETE FROM leo_ads.placement_ad WHERE placement_id IN (
      SELECT DISTINCT placement_id FROM leo_ads.placement WHERE tenant_id = %s
    );
    
    INSERT INTO leo_ads.placement_ad (placement_id, ad_id, rank_score, is_active)
    SELECT 
      a.placement_id,
      a.ad_id,
      a.score_weight,
      (a.status = 'active' AND c.status = 'active' AND p.status = 'active')
    FROM leo_ads.ad a
    JOIN leo_ads.creative c ON c.creative_id = a.creative_id
    JOIN leo_ads.placement p ON p.placement_id = a.placement_id
    WHERE a.tenant_id = %s;
    """
    
    postgres_io.execute(sql, [tenant_id, tenant_id])
    
    # Sync to Redis
    redis_client.publish('ads:index:updated', json.dumps({
      'tenant_id': tenant_id,
      'timestamp': datetime.now().isoformat()
    }))
```

### Task: Sync CDP Audiences

```python
@asset
def sync_cdp_audiences(postgres_io: PostgresIO, cdp_client: CDPClient, tenant_id: int) -> None:
    """Sync audience definitions and membership from CDP."""
    
    # Get all segments from CDP
    segments = cdp_client.list_segments(tenant_id=tenant_id)
    
    for segment in segments:
        # Insert or update audience definition
        postgres_io.execute("""
        INSERT INTO leo_ads.audience 
          (tenant_id, audience_key, name, external_audience_id, 
           membership_version, member_count_estimate, definition)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, audience_key) DO UPDATE
        SET name = EXCLUDED.name,
            member_count_estimate = EXCLUDED.member_count_estimate,
            membership_version = leo_ads.audience.membership_version + 1,
            updated_at = now()
        """, [
            tenant_id,
            segment['key'],
            segment['name'],
            segment['id'],
            1,
            segment['member_count'],
            json.dumps(segment['definition'])
        ])
```

### Task: Aggregate Performance Metrics

```python
@asset
def performance_aggregates(postgres_io: PostgresIO) -> None:
    """Aggregate ad events by hour for reporting."""
    
    sql = """
    INSERT INTO ads.performance_hourly 
      (hour, tenant_id, ad_id, creative_id, campaign_id, 
       impressions, clicks, conversions, revenue)
    SELECT 
      DATE_TRUNC('hour', ae.event_time),
      ae.tenant_id,
      ae.ad_id,
      ae.creative_id,
      ae.campaign_id,
      SUM(CASE WHEN ae.event_type = 'impression' THEN 1 ELSE 0 END),
      SUM(CASE WHEN ae.event_type = 'click' THEN 1 ELSE 0 END),
      SUM(CASE WHEN ae.event_type = 'conversion' THEN 1 ELSE 0 END),
      SUM(CASE WHEN ae.event_type = 'conversion' THEN ae.revenue_amount ELSE 0 END)
    FROM leo_ads.ad_event ae
    WHERE ae.event_time >= DATE_TRUNC('hour', now()) - INTERVAL '2 hours'
    GROUP BY 1, 2, 3, 4, 5
    ON CONFLICT (hour, tenant_id, ad_id, creative_id, campaign_id) DO UPDATE
    SET impressions = EXCLUDED.impressions,
        clicks = EXCLUDED.clicks,
        conversions = EXCLUDED.conversions,
        revenue = EXCLUDED.revenue
    """
    
    postgres_io.execute(sql)
```

---

## Data Management Best Practices

### Multi-Tenancy
- ✅ All queries MUST filter by `tenant_id`
- ✅ Use composite keys: `(tenant_id, resource_key)` for uniqueness
- ✅ Archive instead of delete to preserve audit trail
- ✅ Validate tenant isolation in integration tests

### Schema Evolution
- ✅ Use JSONB columns for extensible data (avoid schema migrations)
- ✅ Never modify constraint logic (business rules in application)
- ✅ Add new columns as nullable or with defaults
- ✅ Deprecate old fields; migrate data before dropping

### Performance
- ✅ Index all foreign keys and commonly filtered columns
- ✅ Partition large tables by time (ad_event)
- ✅ Use materialized views (placement_ad) for complex joins
- ✅ Cache frequently accessed data in Redis (placements, audiences)

### Data Quality
- ✅ Implement NOT NULL constraints for required fields
- ✅ Use CHECK constraints for value validation (status codes, numbers)
- ✅ Use unique constraints for business keys
- ✅ Validate foreign key relationships (referential integrity)

### Audit & Compliance
- ✅ Track created_at, updated_at on all audit tables
- ✅ Use soft deletes (status='archived') instead of hard deletes
- ✅ Log all data modifications (insert, update, delete) for compliance
- ✅ Hash user IDs (user_key_hash) to reduce PII storage

---

## Scripts

### db-schema-init.sql
**Purpose:** Initialize the complete schema.
- Creates tables, indexes, constraints
- Inserts reference data (source_type, render_type, destination_type)
- Idempotent (use `CREATE TABLE IF NOT EXISTS`)

**Usage:**
```bash
psql -U postgres -d customer360 -f db-schema-init.sql
```

### sample-data-init.sql
**Purpose:** Seed demo data for development and testing.
- Inserts tenant, advertisers, creatives, ads, audiences
- Realistic Vietnamese pricing and content
- A/B test variants, multi-channel campaigns
- Safe to run repeatedly (uses `ON CONFLICT`)

**Usage:**
```bash
psql -U postgres -d customer360 -f sample-data-init.sql
```

---

## Related Documentation

- **API Documentation:** See [../README.md](../README.md) for API endpoints
- **Sample Data Details:** See [SAMPLE_DATA_EXPLAINATION.md](SAMPLE_DATA_EXPLAINATION.md) for semantic explanation
- **Dagster Workflows:** See `backend-system/` for task implementations
- **Database Utilities:** See `database-init/` for additional SQL utilities

---

## Support & Troubleshooting

### Common Issues

**Issue: Constraint violation on insert**
```
ERROR: duplicate key value violates unique constraint
```
**Solution:** Check if record already exists (ON CONFLICT clause in insert) or if business key collision.

**Issue: Foreign key error**
```
ERROR: insert or update on table violates foreign key constraint
```
**Solution:** Ensure referenced parent record exists and tenant_id matches.

**Issue: Slow query**
**Solution:** Run `EXPLAIN (ANALYZE, BUFFERS)` to check execution plan; ensure indexes exist on filtered columns.

---

## Summary

This schema provides a flexible, scalable foundation for multi-tenant ad serving. Key principles:

1. **Separation of concerns:** Business logic (Campaign) separate from content (Creative) and delivery (Ad/Placement)
2. **Multi-tenancy:** `tenant_id` on all tables, composite unique keys
3. **Extensibility:** JSONB for provider-specific data without schema migrations
4. **Performance:** Indexed hot path, materialized views, partitioned events
5. **Auditability:** Soft deletes, created_at/updated_at, preserved provider payloads

Dagster admin tasks orchestrate:
- Campaign lifecycle (create, activate, pause, archive)
- A/B testing and optimization
- Audience syncing and targeting
- Budget management and pacing
- Provider account reconciliation
- Performance aggregation and reporting
