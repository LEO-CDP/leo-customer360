# Sample Data Explanation

This document explains **WHAT** data is seeded in `sample-data-init.sql`, **WHY** each data element exists, and **WHEN** it's used during ad serving and campaign management.


---

## Data Seeding Philosophy

The sample data represents a **realistic, multi-tenant Vietnamese e-commerce advertising scenario**. It includes:
- Multiple advertisers operating simultaneously
- Cross-channel campaigns (direct, Google, Shopee, Lazada affiliates)
- Real-world pricing in VND (Vietnamese Dong)
- Authentic A/B testing scenarios
- Realistic user behaviors and event distributions
- Complete targeting configurations

**Purpose:** Enable developers, QA, and stakeholders to test end-to-end ad serving workflows without requiring external data sources.

---

## Data Layers & Meanings

### Layer 1: Tenant Foundation

**WHAT:** Single tenant named "demo" with Vietnamese regional settings

**WHY:**
- Multi-tenant isolation is core to ad server design
- Tenant controls all data access and settings
- All subsequent entities must belong to this tenant

**WHEN USED:**
- Every API request filters by `tenant_id = 1`
- Audit trails track tenant-level operations
- Settings control currency (VND), country (VN), environment

```json
{
  "tenant_key": "demo",
  "name": "LEO Ad Server Demo",
  "country": "VN",
  "currency": "VND",
  "environment": "demo"
}
```

---

### Layer 2: Advertisers & Accounts

**WHAT:** 3 brands representing different business models

#### Coolmate (Direct Advertiser)
- **Business Model:** Direct e-commerce brand (apparel)
- **WHY:** Tests internal campaign management and direct placements
- **WHEN USED:** 
  - When admin creates campaigns directly in the system
  - Source account type: "local" (internal control)
  - Example: Summer sale campaign (50M VND budget)

#### ABC Fashion (Affiliate Partner)
- **Business Model:** Premium affiliate/brand partnership
- **WHY:** Tests partner integrations and affiliate network handling
- **WHEN USED:**
  - When integrating with affiliate providers (Shopee, Lazada)
  - Validates revenue sharing and performance tracking
  - Example: Seasonal retargeting campaign via Shopee

#### TechStore Vietnam (Electronics)
- **Business Model:** Multi-category electronics retailer
- **WHY:** Tests cross-category campaign logic
- **WHEN USED:**
  - Demonstrates category-specific creative variants
  - Electronics-specific pricing and product attributes
  - Example: Tech awareness campaign (20M VND budget)

**Source Accounts (Provider Integration):**

| Provider | Account Key | Type | WHEN USED |
|----------|------------|------|-----------|
| internal | coolmate_internal | Local | Direct Coolmate campaigns |
| google_ads | gam_demo_account | Ad Network | Google Ad Manager integration |
| shopee_affiliate | shopee_demo_account | Affiliate | Shopee marketplace syndication |
| lazada_affiliate | lazada_demo_account | Affiliate | Lazada marketplace syndication |

**WHY multiple accounts:** Real advertisers use multiple channels simultaneously to maximize reach.

---

### Layer 3: Placements (Publisher Inventory)

**WHAT:** 10 inventory slots representing different publisher positions and devices

**WHY:** Placements define where ads can appear. Different placements have:
- Different performance characteristics
- Different audience composition
- Different daily capacity limits
- Different size constraints

**WHEN USED:** During ad serving, placement determines:
1. Which ads are eligible to show
2. Which creative sizes are acceptable
3. Whether frequency caps are enforced
4. Performance attribution

#### Placement Inventory Map

| Placement | Device | Type | Daily Cap | WHY |
|-----------|--------|------|-----------|-----|
| **coolmate-banner-300x250** | Desktop | Standard | 5,000 impr | High-performing desktop placement for e-commerce |
| **coolmate-banner-mobile** | Mobile | Responsive | 8,000 impr | Mobile banner (reduced height for thumb access) |
| **coolmate-product-carousel** | All | Responsive | 3,000 impr | Carousel feed (limited to reduce user fatigue) |
| **coolmate-native-article** | All | Native | 2,000 impr | Native ads in article content (low volume, high intent) |
| **coolmate-search-ads** | Mobile | Search | 6,000 impr | Mobile search (high intent, captures mobile shoppers) |
| **google-top-banner** | Desktop | Flexible | 4,000 impr | Premium top banner on Google partner network |
| **google-native** | All | Responsive | 2,500 impr | Google native ad format (contextual) |
| **shopee-sidebar** | Desktop | Standard | 7,000 impr | Shopee marketplace sidebar (affiliate channel) |
| **lazada-interstitial** | Mobile | Responsive | 3,500 impr | Lazada mobile interstitial (intrusive but high-impact) |
| **generic-feed-carousel** | All | Responsive | 4,500 impr | Generic feed carousel (cross-platform) |

**Daily caps WHY:**
- Prevent over-saturation (user fatigue, declining CTR)
- Manage publisher inventory fairly
- Enforce publisher rate limits
- Simulate realistic ad exchange dynamics

---

### Layer 4: Campaigns

**WHAT:** 4-5 active campaigns with different objectives and budgets

**WHY:** Campaigns represent business intentions:
- Drive product sales (Conversions objective, CPC/CPA buying model)
- Build brand awareness (Awareness objective, CPM buying model)
- Generate traffic (Traffic objective, CPM buying model)

**WHEN USED:**
- Campaign budget controls total spend across all ads
- Campaign objective determines optimization direction
- Campaign status (active/paused) controls serving eligibility
- Campaign dates enforce temporal boundaries

#### Campaign Details

**Campaign 1: Coolmate Summer Sale 2026**
- **Advertiser:** Coolmate
- **Objective:** Conversions (primary goal: purchase)
- **Buying Model:** CPC (Cost-Per-Click) - pay only for clicks
- **Budget:** 50M VND total, 500K daily
- **Status:** Active
- **Duration:** 2026-06-01 to 2026-08-31
- **WHY:** Seasonal campaign targeting shopping season; CPC model reduces risk (pay only for engagement)
- **WHEN USED:** 
  - At serving time: Filter ads by campaign status/dates
  - At billing: Calculate CPC costs per click event
  - At reporting: Aggregate metrics by campaign

**Campaign 2: ABC Fashion Retargeting**
- **Advertiser:** ABC Fashion
- **Objective:** Conversions (cart abandonment recovery)
- **Buying Model:** CPA (Cost-Per-Action) - pay per purchase
- **Budget:** 30M VND
- **WHY:** Retargeting has high conversion rates; CPA aligns costs with actual conversions
- **WHEN USED:**
  - At serving: Only show ads to users with recent cart abandonment
  - At billing: Calculate CPA costs per conversion event

**Campaign 3: TechStore Awareness**
- **Advertiser:** TechStore Vietnam
- **Objective:** Awareness (brand visibility)
- **Buying Model:** CPM (Cost-Per-1000-Impressions) - pay per thousand views
- **Budget:** 20M VND
- **WHY:** Awareness campaigns prioritize reach over immediate conversion; CPM offers guaranteed impressions
- **WHEN USED:**
  - At serving: Maximize impressions (don't restrict by conversion likelihood)
  - At billing: Calculate CPM costs per 1,000 impressions

---

### Layer 5: Creatives (Ad Content)

**WHAT:** 10+ creative assets representing different messaging and variants

**WHY:** Creatives are the actual ad content users see. Multiple creatives enable:
- A/B testing different messages
- Testing different formats (banner, native, carousel)
- Platform-specific optimization (desktop vs. mobile)
- Seasonal/contextual variations

**WHEN USED:**
- At serving: Select which creative to show based on placement/user
- At analytics: Track performance metrics per creative
- At optimization: Identify winning variants and pause underperformers

#### Creative A/B Test Example: Coolmate Summer Banner

**Variant A (Control):**
```
Headline: "Khuyến mãi Hè 2026"
Subheadline: "Giảm giá đến 40%"
Image: Standard summer sale image
Priority: 100 (highest)
WHY: Conservative messaging, established brand trust approach
```

**Variant B (Test - Urgency):**
```
Headline: "Chỉ còn lại 2 ngày! ⏰"
Subheadline: "Giảm giá đến 40% - Mua ngay!"
Image: High-energy summer sale with countdown timer
Priority: 95 (slightly lower)
WHY: Urgency messaging tests whether FOMO increases clicks/conversions
```

**When used:**
- Both variants run simultaneously on same placement
- Analytics compares CTR and conversion rate
- Winning variant gets higher score_weight after 1-2 weeks
- Losing variant is paused

#### Creative Platform Variants

Some creatives optimized for specific devices:

| Creative Key | Device | Format | WHY |
|--------------|--------|--------|-----|
| retargeting_dynamic_01_mobile | Mobile | 320x100 | Responsive height for mobile screens |
| retargeting_dynamic_01_desktop | Desktop | 300x250 | Standard desktop square |
| product_carousel_01_mobile | Mobile | 100% width, 3 items | Mobile thumb-friendly carousel |
| product_carousel_01_desktop | Desktop | 100% width, 4 items | Desktop landscape orientation allows more items |

**WHY platform variants:** Same message may perform differently on mobile vs. desktop. Different devices have different ergonomics.

---

### Layer 6: Creative Items (Carousel Products)

**WHAT:** 9 products in product carousel with realistic Vietnamese e-commerce attributes

**WHY:** Product carousels drive e-commerce conversions by:
- Showcasing multiple items per ad (increasing relevance)
- Displaying real-time pricing and discounts
- Building social proof (ratings, reviews)
- Enabling quick browsing without leaving publisher

**WHEN USED:**
- At serving: Display carousel creative on placements supporting product carousel format
- At click: User clicks product → navigates to product page
- At conversion: Track which product was purchased (via product_id in conversion event)

#### Product Attributes & WHY

| Product | Price | Original | Discount | Rating | Stock | WHY |
|---------|-------|----------|----------|--------|-------|-----|
| Áo sơ mi nam Classic | 399K | 499K | -20% | 4.8⭐ | 45 | Premium shirt, high discount, best-seller (high review count) |
| Áo thun nam Essential | 249K | 275K | -9% | 4.6⭐ | 112 | Budget option, good stock, trending |
| Áo polo nam Premium | 425K | 475K | -10% | 4.7⭐ | 67 | High-end option, seasonal demand |
| Quần shorts nam chino | 449K | 549K | -18% | 4.9⭐ | 89 | Summer seasonal, large discount attracts buyers |

**Vietnamese pricing rationale:**
- 199K-499K range matches real Coolmate apparel pricing
- 20% average discount reflects e-commerce norm
- Stock levels (45-156 units) suggest in-stock assurance
- Ratings (4.6-4.9) reflect authentic e-commerce high-volume sellers

**WHEN USED AT DIFFERENT STAGES:**

1. **At Ad Creation:** Admin configures which products appear in carousel
2. **At Serving:** Platform fetches current price/stock/rating for each product
3. **At Click:** User clicks product in carousel → navigates to product page
4. **At Analytics:** Platform tracks which products were viewed/clicked/purchased

---

### Layer 7: Audience Segments

**WHAT:** 8 audience definitions representing different user segments and behaviors

**WHY:** Audiences enable targeting:
- Retargeting (users who viewed products but didn't buy)
- Lookalike (users similar to buyers)
- Exclusion (users already customers → don't re-acquire)
- Behavioral (users showing purchase intent)

**WHEN USED:**
- At campaign setup: Admin selects which audiences to target
- At serving: Ad server checks if user is in included/excluded audiences
- At optimization: Analytics identifies which audiences have highest ROI

#### Audience Definitions

**High-Value (LTV > 3M VND)**
```
Definition: Customers with lifetime value exceeding 3 million VND
Member Count: ~35,000 users
Lookback: All-time (permanent segment)
WHY: Target existing high-spenders with premium offers
WHEN USED: Premium campaigns, exclusive deals
```

**Cart Abandoners (7-day)**
```
Definition: Users who added items to cart but didn't purchase in last 7 days
Member Count: ~52,000 users
Lookback: 7 days (dynamic, updates hourly)
WHY: Cart abandoners have high conversion intent; strategic timing increases recovery
WHEN USED: ABC Fashion retargeting campaign, time-sensitive offers
```

**Recent Product Viewers (30-day)**
```
Definition: Users who viewed product pages in last 30 days
Member Count: ~125,000 users
Lookback: 30 days
WHY: Strong purchase intent signal; users actively shopping
WHEN USED: Retargeting campaigns, product-specific ads
```

**Mobile Users VN**
```
Definition: Users with mobile device in Vietnam (7-day active)
Member Count: ~285,000 users
Lookback: 7 days (active in last week)
WHY: Mobile users respond to mobile-optimized creatives
WHEN USED: Mobile placement campaigns, mobile-specific formats
```

**New Users (30-day)**
```
Definition: Users with first visit in last 30 days
Member Count: ~18,000 users
WHY: New users need brand education; use different creative approach
WHEN USED: Brand awareness campaigns, onboarding messages
```

**Seasonal Summer Shoppers (60-day)**
```
Definition: Users showing summer seasonal interest + mobile preference
Member Count: ~42,000 users
WHEN USED: Seasonal campaign (June-August), time-limited offers
```

**Repeat Purchasers**
```
Definition: Users with 3+ purchases (lifetime)
Member Count: ~78,000 users
WHY: Loyal customers; test premium products and exclusive offers
```

**Corporate/Bulk Buyers**
```
Definition: Users identified as B2B bulk purchasers
Member Count: ~8,500 users
WHY: Bulk buyers have different purchase patterns; use B2B messaging
```

---

### Layer 8: Targeting Rules

**WHAT:** 8+ conditional rules that determine which users see which ads

**WHY:** Targeting rules maximize relevance and ROI:
- Geographic targeting (show Vietnam ads only in Vietnam)
- Temporal targeting (show urgency messages during peak hours)
- Device targeting (optimize for mobile vs. desktop)
- Behavioral targeting (target cart abandoners differently than new users)
- Intent targeting (detect shopping mode via context)

**WHEN USED:** At serving time, before showing an ad:
```
1. Check if user in required audiences (INCLUDE)
2. Check if user NOT in excluded audiences (EXCLUDE)
3. Evaluate all targeting rules → at least one must match
4. If all rules pass → ad is eligible to show
```

#### Example Rule: Coolmate Retargeting

```
Priority: 100 (highest priority)
Countries: ['VN'] (only Vietnam)
Device Types: ['mobile', 'desktop'] (excludes tablet)
Languages: ['vi'] (Vietnamese language)
Context Keywords: ['fashion', 'menswear', 'style'] (relevant categories)

Custom Predicates:
  - retargeting: true (must be retargeting audience)
  - recentProductViewDays: 30 (viewed product in last 30 days)
  - minTimeOnSite: 30 seconds (spent >30s on site, showing interest)
  - dayOfWeek: Mon-Fri (exclude weekends, different behavior)

WHEN USED: High-priority cart recovery
WHY: Retargeting cart abandoners is highest-intent segment
```

#### Example Rule: Mobile Urgency (Peak Hours)

```
Priority: 95
Device Types: ['mobile'] (only mobile)
Time of Day: ['08:00-12:00', '18:00-23:00'] (morning commute + evening browsing)

Custom Predicates:
  - cartValue: >100,000 VND (higher-value orders only)
  - recentCartAbandonment: true (abandoned cart)
  - daysSinceLastPurchase: 7-30 (not recent buyer)

WHEN USED: Mobile-specific urgency messaging during peak hours
WHY: Mobile users at work/evening more likely to respond to urgency
```

#### Example Rule: Contextual (Article Context)

```
Priority: 80
Context: ['article_page'] (showing on article/news page)
Article Categories: ['fashion', 'lifestyle', 'style'] (relevant content)
Device Types: ['all'] (works on any device)

WHEN USED: Native ads in article content
WHY: Article context matching → higher relevance → better CTR
```

---

### Layer 9: Ads (Delivery Configuration)

**WHAT:** 10 ad configurations linking campaign + creative + placement

**WHY:** Ads tie together:
- **Campaign** (the business intent: "sell summer collection")
- **Creative** (the content: "Khuyến mãi Hè" banner)
- **Placement** (where it shows: "Homepage banner")

This configuration is the minimal serving unit.

**WHEN USED:**
- At serving: Platform fetches eligible ads for a placement
- At ranking: Ads sorted by score_weight (100 = top priority)
- At frequency capping: Limit how many times one ad shows per user
- At attribution: Track performance metrics per ad

#### Ad Configuration Example

```
Ad Key: "ad-coolmate-banner-01"
Campaign: "coolmate-summer-2026" (50M VND budget, conversions objective)
Creative: "summer-sale-banner" (v1 - control variant)
Placement: "coolmate-banner-300x250" (5K daily impressions)
Status: "active"
Score Weight: 100.0 (highest priority)
Frequency Cap: 5x per user (show max 5 times per user)

WHEN USED:
  1. User visits placement "coolmate-banner-300x250"
  2. Platform checks ad eligibility
  3. "ad-coolmate-banner-01" passes all filters
  4. "summer-sale-banner" v1 creative is shown
  5. Impression event logged
  6. If user clicks → click event logged
  7. If user purchases → conversion event logged
  8. Metrics aggregated to campaign for budget tracking
```

#### Ad Serving Index (Placement-Ad Mapping)

For performance, "placement_ad" table pre-computes which ads can serve on which placements:

```sql
placement_id=1, ad_id=1, rank_score=100.0 (highest rank)
placement_id=1, ad_id=2, rank_score=95.0
placement_id=1, ad_id=5, rank_score=80.0
-- ... sorted by score for fast candidate selection
```

**WHY:** Instead of scanning all 10 ads every request, platform pre-filters eligible ads per placement.

---

### Layer 10: User Serving Profiles

**WHAT:** 10 synthetic user profiles with attributes for targeting

**WHY:** User profiles enable:
- Segment-based targeting (show ads only to high-value users)
- Device-based personalization (desktop vs. mobile creative)
- Interest-based matching (fashion users → fashion ads)
- LTV-based prioritization (spend more on high-value users)

**WHEN USED:** At serving time:
```
1. User visits placement
2. Platform fetches user profile (user_serving_key)
3. Platform checks if user matches targeting rules
   - Is user in "high-value" audience? ✓
   - Is user on mobile? ✓
   - Is user in Vietnam? ✓
4. If all checks pass → show ad
```

#### User Segment Examples

**User Profile 1: High-Value Retargeter**
```
LTV: 5M VND (high spender)
Purchase Count: 12 (loyal)
Primary Device: mobile
Segment: retargeting (cart abandonment)
Interests: fashion, sales, menswear
WHY: Show premium products + urgency messaging
```

**User Profile 2: New Visitor**
```
LTV: 0 VND (no purchases)
Purchase Count: 0
Primary Device: desktop
Segment: first_visit (just arrived)
Interests: (unknown)
WHY: Show brand intro + entry-level products
```

**User Profile 3: Seasonal Summer Shopper**
```
LTV: 3.2M VND
Purchase Count: 5 (occasional)
Primary Device: mobile
Seasonal Interest: summer, menswear
Purchase Pattern: Q2-Q3 focus
WHY: Show summer-specific products during peak season
```

---

### Layer 11: Sample Events (Realistic Funnel)

**WHAT:** 50+ events representing user interactions (impressions, clicks, conversions)

**WHY:** Events are the raw data for:
- Performance analytics (CTR, conversion rate)
- Budget tracking (cost accumulation)
- Attribution modeling (multi-touch paths)
- Campaign optimization (identify winning ads)

**WHEN USED:**
- At runtime: Events generated as users interact with ads
- At analytics: Events aggregated into reports and dashboards
- At billing: Events used to calculate costs (CPC, CPA, CPM)

#### Event Distribution Rationale

```
Timeline:  T-6h → T-1h  (impressions spread over 6 hours)
Impressions: 72 total
  ├─ Desktop: 35 impressions (mix of banners, natives)
  └─ Mobile: 37 impressions (mobile-optimized placements)

Clicks: 12 total (~2.5% CTR = realistic for e-commerce)
  ├─ From retargeting users: 8 clicks (high intent)
  └─ From new users: 4 clicks (lower intent)

Conversions: 9 total (~75% of clicks convert = realistic)
  ├─ Direct clicks: 9 conversions
  └─ Revenue: 249K + 199K + etc. (product-dependent)

VTC (View-Through): 1 conversion
  └─ User saw ad but didn't click, converted later
  └─ Demonstrates view-based attribution value
```

#### Event Type Explanations

**Impression Event:**
```json
{
  "event_type": "impression",
  "user_id": "demo-user-001",
  "ad_id": 1,
  "placement_id": 1,
  "device": "mobile",
  "country": "VN",
  "timestamp": "2026-08-15 14:32:15",
  "duration_seconds": 3.2,
  "WHY": Track when ad was shown to user
}
```

**Click Event:**
```json
{
  "event_type": "click",
  "user_id": "demo-user-001",
  "ad_id": 1,
  "placement_id": 1,
  "destination_url": "https://coolmate.com/product/1",
  "timestamp": "2026-08-15 14:33:45",
  "WHY": Track engagement - user clicked ad
}
```

**Conversion Event (Purchase):**
```json
{
  "event_type": "conversion",
  "event_subtype": "purchase",
  "user_id": "demo-user-001",
  "ad_id": 1,
  "campaign_id": 1,
  "product_id": "p2",
  "revenue": 249000,
  "currency": "VND",
  "timestamp": "2026-08-15 14:48:30",
  "time_to_conversion_seconds": 945,
  "WHY": Track business outcome - user purchased
}
```

**VTC Event (View-Through Conversion):**
```json
{
  "event_type": "conversion",
  "attribution_type": "view_through",
  "user_id": "demo-user-002",
  "ad_id": 2,
  "revenue": 199000,
  "timestamp": "2026-08-15 16:20:00",
  "hours_since_impression": 2.5,
  "WHY": Track delayed conversions from views alone (no click)
}
```

---

## Data Flow Timeline

### Campaign Lifecycle with Sample Data

```
T0 (Campaign Creation):
  Admin creates campaign "coolmate-summer-2026"
  → INSERT leo_ads.campaign (budget_amount=50M, status='draft')
  
T1 (Content Creation):
  Admin creates creatives and carousel products
  → INSERT leo_ads.creative (headline, body, images)
  → INSERT leo_ads.creative_item (products, prices, stock)
  
T2 (Targeting Configuration):
  Admin defines audiences and rules
  → INSERT leo_ads.audience (high_value_ltv, cart_abandoners)
  → INSERT leo_ads.targeting_rule (countries, devices, custom_predicates)
  
T3 (Ad Creation):
  Admin creates ad linking campaign + creative + placement
  → INSERT leo_ads.ad (campaign_id, creative_id, placement_id)
  → Platform computes placement_ad index
  
T4 (Activation):
  Admin activates campaign
  → UPDATE leo_ads.campaign SET status='active'
  
T5 (Serving):
  User visits placement → Platform selects ad → Shows creative
  → INSERT leo_ads.ad_event (type='impression')
  
T6 (Engagement):
  User clicks ad → Browser redirects to destination URL
  → INSERT leo_ads.ad_event (type='click')
  → User views product page
  
T7 (Conversion):
  User purchases product
  → INSERT leo_ads.ad_event (type='conversion', revenue=249K)
  → UPDATE leo_ads.campaign SET spent_amount = spent_amount + cost
  
T8 (Analytics):
  Campaign runs for 2 months
  Platform aggregates events → Reports show:
  → 72 impressions, 12 clicks (16.7% CTR), 9 conversions (75% conv rate)
  → ROI calculated: 9 × 249K revenue vs. actual CPC cost
```

---

## Why Each Element Matters

| Element | Why It Matters | Real-World Use |
|---------|----------------|----------------|
| **Tenant** | Isolation & security | SaaS multi-customer data protection |
| **Advertiser** | Business entity | Track campaigns by brand |
| **Campaign** | Business objective | Budget allocation, performance goal |
| **Creative** | Message testing | A/B test messaging, formats |
| **Placement** | Ad location | Optimize by publisher/position/device |
| **Ad** | Serving configuration | Minimal unit of serving decision |
| **Audience** | User targeting | Reach right person, right time |
| **Targeting Rule** | Eligibility conditions | Context-based + behavioral filtering |
| **Event** | Business outcome | Attribution, optimization, billing |
| **User Profile** | Personalization | Segment-based creative selection |

---

## Verification Queries

After seed script runs, verify data integrity:

### Check Campaign Budget Tracking Setup
```sql
SELECT 
  c.campaign_key, 
  c.budget_amount, 
  c.daily_budget_amount,
  c.status,
  COUNT(a.ad_id) as ad_count
FROM leo_ads.campaign c
LEFT JOIN leo_ads.ad a ON a.campaign_id = c.campaign_id
GROUP BY c.campaign_id, c.campaign_key;
```

### Check A/B Test Variant Setup
```sql
SELECT 
  creative_key,
  version_no,
  priority,
  status
FROM leo_ads.creative
ORDER BY creative_key, version_no;
```

### Check Audience Targeting Links
```sql
SELECT 
  aud.audience_key,
  COUNT(aa.ad_id) as ads_targeted,
  SUM(CASE WHEN aa.relation_type='include' THEN 1 ELSE 0 END) as includes,
  SUM(CASE WHEN aa.relation_type='exclude' THEN 1 ELSE 0 END) as excludes
FROM leo_ads.audience aud
LEFT JOIN leo_ads.ad_audience aa ON aa.audience_id = aud.audience_id
GROUP BY aud.audience_id, aud.audience_key;
```

### Check Event Distribution (Funnel)
```sql
SELECT 
  event_type,
  COUNT(*) as event_count,
  ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM leo_ads.ad_event), 1) as pct
FROM leo_ads.ad_event
GROUP BY event_type
ORDER BY event_count DESC;
```

### Check Placement-Ad Index (Serving Candidates)
```sql
SELECT 
  p.placement_key,
  COUNT(pa.ad_id) as eligible_ads,
  STRING_AGG(a.ad_key, ', ' ORDER BY pa.rank_score DESC) as ad_candidates
FROM leo_ads.placement p
LEFT JOIN leo_ads.placement_ad pa ON pa.placement_id = p.placement_id
LEFT JOIN leo_ads.ad a ON a.ad_id = pa.ad_id
WHERE p.tenant_id = 1
GROUP BY p.placement_id, p.placement_key;
```

---

## Context for Development & Testing

**For QA Testing:**
- Full campaign lifecycle end-to-end
- A/B testing scenario validation
- Targeting rule evaluation
- Event tracking accuracy
- Frequency capping enforcement

**For Dashboards & Analytics:**
- Sample data populates reports
- Realistic metrics (CTR ~2.5%, conversion ~75% of clicks)
- Vietnamese pricing for localization testing
- Multi-channel campaign comparison

**For Performance Testing:**
- 50+ events exercise event processing
- 10 placements × 10 ads test index performance
- 10 users × 8 audiences test targeting logic
- Materialized view queries demonstrate optimization

**For Integration Testing:**
- Multiple provider accounts (local, Google, Shopee, Lazada)
- Source asset mapping validates provider sync
- Event payload structure matches real providers

---

## Documentation

For schema details, see: [README.md](README.md) (comprehensive schema documentation)
For API usage, see: [../README.md](../README.md) (ad server API endpoints)
For full database context, see: [../../database-init/database-schema.sql](../../database-init/database-schema.sql)
