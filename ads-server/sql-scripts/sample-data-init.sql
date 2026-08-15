-- ============================================================================
-- LEO ADS / HIGH-SCALE AD SERVER
-- DEMO / SAMPLE DATA SEED
-- PostgreSQL 16+
--
-- Purpose:
--   Seed a small but representative demo dataset for the leo_ads schema.
--
-- Covers:
--   - Local / internal ads
--   - Google Ad Manager JS-tag ads
--   - Shopee affiliate native product ads
--   - Lazada affiliate JS widget
--   - Single banner
--   - Native card
--   - Product carousel
--   - External JS widget
--   - Targeting rules / audiences
--   - Precomputed placement -> ad serving index
--   - Impression/click tracking endpoints
--
-- The data is intentionally small. It is designed to exercise the schema and
-- Ad API locally, NOT to simulate production traffic volume.
--
-- Safe to run repeatedly:
--   Uses stable business keys and ON CONFLICT DO NOTHING.
--
-- Prerequisite:
--   Run db-schema-high-scale-ad-server.sql first.
-- ============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS leo_ads;

-- ============================================================================
-- 1. TENANT
-- ============================================================================

INSERT INTO leo_ads.tenant (
    tenant_key,
    name,
    status,
    settings
)
VALUES (
    'demo',
    'LEO Ad Server Demo',
    'active',
    '{
      "country": "VN",
      "currency": "VND",
      "environment": "demo"
    }'::jsonb
)
ON CONFLICT (tenant_key) DO NOTHING;


-- ============================================================================
-- 2. ADVERTISERS
-- ============================================================================

INSERT INTO leo_ads.advertiser (
    tenant_id,
    advertiser_key,
    name,
    description,
    title,
    logo_url,
    metadata
)
SELECT
    t.tenant_id,
    x.advertiser_key,
    x.name,
    x.description,
    x.title,
    x.logo_url,
    x.metadata::jsonb
FROM leo_ads.tenant t
CROSS JOIN (
    VALUES
    (
        'coolmate',
        'Coolmate',
        'Thời trang nam giới hiện đại',
        'Coolmate',
        'https://ui-avatars.com/api/?name=Coolmate&background=000&color=fff&size=128',
        '{"category":"fashion","country":"VN"}'
    ),
    (
        'abc-fashion',
        'ABC Fashion',
        'Thời trang nam từ đối tác affiliate',
        'ABC Fashion',
        'https://ui-avatars.com/api/?name=ABC+Fashion&background=ff6600&color=fff&size=128',
        '{"category":"fashion","country":"VN","affiliate":true}'
    )
) AS x(advertiser_key, name, description, title, logo_url, metadata)
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, advertiser_key) DO NOTHING;


-- ============================================================================
-- 3. SOURCE ACCOUNTS
-- ============================================================================

-- Internal / local campaign source
INSERT INTO leo_ads.source_account (
    tenant_id,
    advertiser_id,
    source_type_code,
    provider_key,
    network_key,
    account_key,
    status,
    config
)
SELECT
    t.tenant_id,
    a.advertiser_id,
    'local',
    'internal',
    'leo_internal',
    'coolmate_internal',
    'active',
    '{"mode":"managed","owner":"demo"}'::jsonb
FROM leo_ads.tenant t
JOIN leo_ads.advertiser a
  ON a.tenant_id = t.tenant_id
 AND a.advertiser_key = 'coolmate'
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, provider_key, account_key) DO NOTHING;


-- Google Ad Manager
INSERT INTO leo_ads.source_account (
    tenant_id,
    advertiser_id,
    source_type_code,
    provider_key,
    network_key,
    account_key,
    publisher_id,
    status,
    config
)
SELECT
    t.tenant_id,
    NULL,
    'ad_network',
    'google_ads',
    'google_ad_manager',
    'gam_demo_account',
    'pub-1234567890',
    'active',
    '{
      "mode":"js_tag",
      "publisherId":"pub-1234567890"
    }'::jsonb
FROM leo_ads.tenant t
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, provider_key, account_key) DO NOTHING;


-- Shopee affiliate
INSERT INTO leo_ads.source_account (
    tenant_id,
    advertiser_id,
    source_type_code,
    provider_key,
    network_key,
    account_key,
    status,
    config
)
SELECT
    t.tenant_id,
    a.advertiser_id,
    'affiliate',
    'shopee_affiliate',
    'shopee',
    'shopee_demo_account',
    'active',
    '{
      "mode":"native",
      "country":"VN"
    }'::jsonb
FROM leo_ads.tenant t
JOIN leo_ads.advertiser a
  ON a.tenant_id = t.tenant_id
 AND a.advertiser_key = 'abc-fashion'
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, provider_key, account_key) DO NOTHING;


-- Lazada affiliate
INSERT INTO leo_ads.source_account (
    tenant_id,
    advertiser_id,
    source_type_code,
    provider_key,
    network_key,
    account_key,
    status,
    config
)
SELECT
    t.tenant_id,
    NULL,
    'affiliate',
    'lazada_affiliate',
    'lazada',
    'lazada_demo_account',
    'active',
    '{
      "mode":"js_tag",
      "publisherId":"publisher_12345"
    }'::jsonb
FROM leo_ads.tenant t
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, provider_key, account_key) DO NOTHING;


-- ============================================================================
-- 4. PLACEMENTS
-- ============================================================================

INSERT INTO leo_ads.placement (
    tenant_id,
    placement_key,
    name,
    status,
    min_width_px,
    max_width_px,
    min_height_px,
    max_height_px,
    responsive,
    metadata
)
SELECT
    t.tenant_id,
    x.placement_key,
    x.name,
    'active',
    x.min_width,
    x.max_width,
    x.min_height,
    x.max_height,
    x.responsive,
    x.metadata::jsonb
FROM leo_ads.tenant t
CROSS JOIN (
    VALUES
    (
        'coolmate-banner-300x250',
        'Coolmate Dynamic Retargeting Banner',
        300, 300, 250, 250, false,
        '{"position":"homepage_top","demoPlacementId":"12345"}'
    ),
    (
        'coolmate-product-carousel',
        'Coolmate Product Carousel',
        NULL, NULL, NULL, NULL, true,
        '{"position":"homepage_feed","demoPlacementId":"12346"}'
    ),
    (
        'coolmate-native',
        'Coolmate Native Sponsored Content',
        NULL, NULL, NULL, NULL, true,
        '{"position":"article_inline","demoPlacementId":"12347"}'
    ),
    (
        'google-top-banner',
        'Google Ad Manager Top Banner',
        NULL, NULL, 90, 250, true,
        '{"provider":"google_ad_manager","demoPlacementId":"google_top_banner"}'
    ),
    (
        'google-native',
        'Google Ad Manager Native',
        NULL, NULL, NULL, NULL, true,
        '{"provider":"google_ad_manager","demoPlacementId":"google_native_001"}'
    ),
    (
        'affiliate-sidebar',
        'Affiliate Sidebar 300x600',
        300, 300, 600, 600, false,
        '{"position":"sidebar","demoPlacementId":"affiliate_sidebar_001"}'
    )
) AS x(
    placement_key,
    name,
    min_width,
    max_width,
    min_height,
    max_height,
    responsive,
    metadata
)
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, placement_key) DO NOTHING;


-- Placement format capabilities

INSERT INTO leo_ads.placement_format (
    placement_id,
    format_code,
    width_px,
    height_px,
    width_unit,
    height_unit,
    responsive,
    constraints
)
SELECT p.placement_id, x.format_code, x.width_px, x.height_px,
       x.width_unit, x.height_unit, x.responsive, x.constraints::jsonb
FROM leo_ads.placement p
JOIN leo_ads.tenant t ON t.tenant_id = p.tenant_id
JOIN (
    VALUES
    ('coolmate-banner-300x250', 'single_banner', 300, 250, 'px', 'px', false, '{}'),
    ('coolmate-product-carousel', 'product_carousel', NULL, NULL, '%', 'auto', true, '{"maxItems":8}'),
    ('coolmate-native', 'native', NULL, NULL, '%', 'auto', true, '{"style":"card"}'),
    ('google-top-banner', 'display', 728, 90, 'px', 'px', true, '{"responsiveSizes":[[728,90],[970,250],[320,100]]}'),
    ('google-native', 'native', 1, 1, 'px', 'px', true, '{"googleNative":true}'),
    ('affiliate-sidebar', 'external_widget', 300, 600, 'px', 'px', false, '{"widget":"lazada"}')
) AS x(
    placement_key,
    format_code,
    width_px,
    height_px,
    width_unit,
    height_unit,
    responsive,
    constraints
) ON x.placement_key = p.placement_key
WHERE t.tenant_key = 'demo'
ON CONFLICT (placement_id, format_code) DO NOTHING;


-- ============================================================================
-- 5. SOURCE ASSETS
-- ============================================================================

INSERT INTO leo_ads.source_asset (
    tenant_id,
    source_account_id,
    asset_type,
    external_asset_id,
    campaign_external_id,
    creative_external_id,
    ad_unit_external_id,
    raw_payload,
    status
)
SELECT
    t.tenant_id,
    sa.source_account_id,
    x.asset_type,
    x.external_asset_id,
    x.campaign_external_id,
    x.creative_external_id,
    x.ad_unit_external_id,
    x.raw_payload::jsonb,
    'active'
FROM leo_ads.tenant t
JOIN leo_ads.source_account sa
  ON sa.tenant_id = t.tenant_id
JOIN (
    VALUES
    (
        'internal',
        'coolmate_retargeting_01',
        'cmp_coolmate_retargeting_001',
        'coolmate_retargeting_01',
        NULL,
        '{"source":"internal","adId":"coolmate_dynamic_retargeting_01"}'
    ),
    (
        'internal',
        'coolmate_product_carousel_01',
        'cmp_coolmate_retargeting_001',
        'coolmate_product_carousel_01',
        NULL,
        '{"source":"internal","adId":"coolmate_product_carousel_01"}'
    ),
    (
        'internal',
        'coolmate_native_01',
        'cmp_001',
        'cr_001',
        NULL,
        '{"source":"internal","adId":"coolmate_native_01"}'
    ),
    (
        'google_gam',
        'google_display_01',
        NULL,
        NULL,
        '12345678901',
        '{"provider":"google_ads","network":"google_ad_manager","adUnitId":"12345678901"}'
    ),
    (
        'google_gam',
        'google_native_01',
        NULL,
        NULL,
        '12345678902',
        '{"provider":"google_ads","network":"google_ad_manager","adUnitId":"12345678902"}'
    ),
    (
        'affiliate_offer',
        'shopee_creative_001',
        'shopee_campaign_001',
        'shopee_creative_001',
        NULL,
        '{"provider":"shopee_affiliate","offerId":"shopee_8821"}'
    ),
    (
        'affiliate_widget',
        'lazada_widget_001',
        'lazada_campaign_001',
        'lazada_widget_001',
        NULL,
        '{"provider":"lazada_affiliate","placementId":"sidebar_001"}'
    )
) AS x(
    asset_type,
    external_asset_id,
    campaign_external_id,
    creative_external_id,
    ad_unit_external_id,
    raw_payload
)
  ON (
      (x.asset_type LIKE 'google%' AND sa.provider_key = 'google_ads')
      OR
      (x.asset_type = 'affiliate_offer' AND sa.provider_key = 'shopee_affiliate')
      OR
      (x.asset_type = 'affiliate_widget' AND sa.provider_key = 'lazada_affiliate')
      OR
      (x.asset_type = 'internal' AND sa.provider_key = 'internal')
  )
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, source_account_id, asset_type, external_asset_id)
DO NOTHING;


-- ============================================================================
-- 6. CAMPAIGNS
-- ============================================================================

INSERT INTO leo_ads.campaign (
    tenant_id,
    advertiser_id,
    source_account_id,
    campaign_key,
    name,
    objective,
    buying_model,
    budget_amount,
    currency,
    daily_budget_amount,
    status,
    starts_at,
    ends_at,
    metadata
)
SELECT
    t.tenant_id,
    a.advertiser_id,
    sa.source_account_id,
    x.campaign_key,
    x.name,
    x.objective,
    x.buying_model,
    x.budget_amount,
    x.currency,
    x.daily_budget_amount,
    'active',
    now() - interval '1 day',
    now() + interval '30 days',
    x.metadata::jsonb
FROM leo_ads.tenant t
JOIN (
    VALUES
    (
        'coolmate-retargeting',
        'Coolmate Dynamic Retargeting',
        'retargeting',
        'cpm',
        50000000::numeric,
        'VND',
        3000000::numeric,
        'coolmate',
        'internal',
        'coolmate_internal',
        '{"channel":"web","strategy":"retargeting"}'
    ),
    (
        'coolmate-native-content',
        'Coolmate Sponsored Content',
        'awareness',
        'cpm',
        30000000::numeric,
        'VND',
        2000000::numeric,
        'coolmate',
        'internal',
        'coolmate_internal',
        '{"channel":"native","strategy":"content"}'
    ),
    (
        'google-display-demo',
        'Google Ad Manager Display Demo',
        'reach',
        'cpm',
        NULL,
        NULL,
        NULL,
        NULL,
        'google_ads',
        'gam_demo_account',
        '{"provider":"google_ad_manager"}'
    ),
    (
        'shopee-affiliate-demo',
        'Shopee Affiliate Fashion Demo',
        'conversion',
        'cpa',
        NULL,
        'VND',
        NULL,
        'abc-fashion',
        'shopee_affiliate',
        'shopee_demo_account',
        '{"network":"shopee","commissionRate":0.08}'
    ),
    (
        'lazada-affiliate-demo',
        'Lazada Affiliate Widget Demo',
        'conversion',
        'cpa',
        NULL,
        'VND',
        NULL,
        NULL,
        'lazada_affiliate',
        'lazada_demo_account',
        '{"network":"lazada"}'
    )
) AS x(
    campaign_key,
    name,
    objective,
    buying_model,
    budget_amount,
    currency,
    daily_budget_amount,
    advertiser_key,
    provider_key,
    account_key,
    metadata
) ON TRUE
LEFT JOIN leo_ads.advertiser a
  ON a.tenant_id = t.tenant_id
 AND a.advertiser_key = x.advertiser_key
JOIN leo_ads.source_account sa
  ON sa.tenant_id = t.tenant_id
 AND sa.provider_key = x.provider_key
 AND sa.account_key = x.account_key
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, campaign_key) DO NOTHING;


-- ============================================================================
-- 7. CREATIVES
-- ============================================================================

-- 7.1 Coolmate single banner
INSERT INTO leo_ads.creative (
    tenant_id,
    campaign_id,
    advertiser_id,
    source_asset_id,
    creative_key,
    ad_type,
    format_code,
    render_type_code,
    status,
    priority,
    headline,
    subheadline,
    cta,
    image_url,
    logo_url,
    content_payload
)
SELECT
    t.tenant_id,
    c.campaign_id,
    a.advertiser_id,
    sa.source_asset_id,
    'coolmate_retargeting_01',
    'dynamic_retargeting',
    'single_banner',
    'native_json',
    'active',
    100,
    'Bạn vẫn quan tâm chứ?',
    'Những sản phẩm bạn đã xem đang chờ bạn.',
    'Xem lại ngay',
    'https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?auto=format&fit=crop&w=600&q=85',
    a.logo_url,
    '{
      "badge":{"text":"-9%","position":"top-left"},
      "adId":"coolmate_dynamic_retargeting_01"
    }'::jsonb
FROM leo_ads.tenant t
JOIN leo_ads.advertiser a
  ON a.tenant_id = t.tenant_id
 AND a.advertiser_key = 'coolmate'
JOIN leo_ads.campaign c
  ON c.tenant_id = t.tenant_id
 AND c.campaign_key = 'coolmate-retargeting'
JOIN leo_ads.source_asset sa
  ON sa.tenant_id = t.tenant_id
 AND sa.external_asset_id = 'coolmate_retargeting_01'
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, creative_key, version_no) DO NOTHING;


-- 7.2 Coolmate carousel
INSERT INTO leo_ads.creative (
    tenant_id,
    campaign_id,
    advertiser_id,
    source_asset_id,
    creative_key,
    ad_type,
    format_code,
    render_type_code,
    status,
    priority,
    headline,
    subheadline,
    cta,
    logo_url,
    content_payload
)
SELECT
    t.tenant_id,
    c.campaign_id,
    a.advertiser_id,
    sa.source_asset_id,
    'coolmate_product_carousel_01',
    'dynamic_retargeting',
    'product_carousel',
    'native_json',
    'active',
    90,
    'Sản phẩm bạn có thể quan tâm',
    'Khám phá thêm những lựa chọn phù hợp.',
    'Xem sản phẩm',
    a.logo_url,
    '{
      "label":"Dành riêng cho bạn",
      "maxItems":4,
      "adId":"coolmate_product_carousel_01"
    }'::jsonb
FROM leo_ads.tenant t
JOIN leo_ads.advertiser a
  ON a.tenant_id = t.tenant_id
 AND a.advertiser_key = 'coolmate'
JOIN leo_ads.campaign c
  ON c.tenant_id = t.tenant_id
 AND c.campaign_key = 'coolmate-retargeting'
JOIN leo_ads.source_asset sa
  ON sa.tenant_id = t.tenant_id
 AND sa.external_asset_id = 'coolmate_product_carousel_01'
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, creative_key, version_no) DO NOTHING;


-- 7.3 Coolmate native content
INSERT INTO leo_ads.creative (
    tenant_id,
    campaign_id,
    advertiser_id,
    source_asset_id,
    creative_key,
    ad_type,
    format_code,
    render_type_code,
    status,
    priority,
    headline,
    body,
    cta,
    image_url,
    logo_url,
    content_payload
)
SELECT
    t.tenant_id,
    c.campaign_id,
    a.advertiser_id,
    sa.source_asset_id,
    'coolmate_native_01',
    'sponsored_content',
    'native',
    'native_json',
    'active',
    80,
    'Phong cách nam giới hiện đại cho mọi ngày',
    'Khám phá những sản phẩm được thiết kế để mang lại sự thoải mái và phong cách trong cuộc sống hàng ngày.',
    'Khám phá ngay',
    'https://images.unsplash.com/photo-1496747611176-843222e1e57c?auto=format&fit=crop&w=800&q=85',
    a.logo_url,
    '{
      "contentId":"content_001",
      "label":"Sponsored"
    }'::jsonb
FROM leo_ads.tenant t
JOIN leo_ads.advertiser a
  ON a.tenant_id = t.tenant_id
 AND a.advertiser_key = 'coolmate'
JOIN leo_ads.campaign c
  ON c.tenant_id = t.tenant_id
 AND c.campaign_key = 'coolmate-native-content'
JOIN leo_ads.source_asset sa
  ON sa.tenant_id = t.tenant_id
 AND sa.external_asset_id = 'coolmate_native_01'
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, creative_key, version_no) DO NOTHING;


-- 7.4 Google display
INSERT INTO leo_ads.creative (
    tenant_id,
    campaign_id,
    source_asset_id,
    creative_key,
    ad_type,
    format_code,
    render_type_code,
    status,
    priority,
    content_payload
)
SELECT
    t.tenant_id,
    c.campaign_id,
    sa.source_asset_id,
    'google_ads_display_01',
    'display',
    'google_ads',
    'js_tag',
    'active',
    70,
    '{
      "provider":"google_ads",
      "network":"google_ad_manager",
      "adUnitPath":"/1234567890/example/homepage",
      "sizes":[[728,90],[970,250],[320,100]],
      "targeting":{"page":"homepage","device":"responsive"}
    }'::jsonb
FROM leo_ads.tenant t
JOIN leo_ads.campaign c
  ON c.tenant_id = t.tenant_id
 AND c.campaign_key = 'google-display-demo'
JOIN leo_ads.source_asset sa
  ON sa.tenant_id = t.tenant_id
 AND sa.external_asset_id = 'google_display_01'
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, creative_key, version_no) DO NOTHING;


-- 7.5 Google native
INSERT INTO leo_ads.creative (
    tenant_id,
    campaign_id,
    source_asset_id,
    creative_key,
    ad_type,
    format_code,
    render_type_code,
    status,
    priority,
    content_payload
)
SELECT
    t.tenant_id,
    c.campaign_id,
    sa.source_asset_id,
    'google_ads_native_01',
    'native',
    'google_ads',
    'js_tag',
    'active',
    65,
    '{
      "provider":"google_ads",
      "network":"google_ad_manager",
      "adUnitPath":"/1234567890/example/native",
      "sizes":[[1,1]],
      "native":true
    }'::jsonb
FROM leo_ads.tenant t
JOIN leo_ads.campaign c
  ON c.tenant_id = t.tenant_id
 AND c.campaign_key = 'google-display-demo'
JOIN leo_ads.source_asset sa
  ON sa.tenant_id = t.tenant_id
 AND sa.external_asset_id = 'google_native_01'
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, creative_key, version_no) DO NOTHING;


-- 7.6 Shopee affiliate native product
INSERT INTO leo_ads.creative (
    tenant_id,
    campaign_id,
    advertiser_id,
    source_asset_id,
    creative_key,
    ad_type,
    format_code,
    render_type_code,
    status,
    priority,
    headline,
    body,
    cta,
    image_url,
    logo_url,
    content_payload
)
SELECT
    t.tenant_id,
    c.campaign_id,
    a.advertiser_id,
    sa.source_asset_id,
    'shopee_affiliate_001',
    'affiliate',
    'native_product',
    'native_json',
    'active',
    60,
    'Áo thun nam Premium - giảm 25%',
    'Ưu đãi độc quyền từ đối tác affiliate.',
    'Xem sản phẩm',
    'https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?auto=format&fit=crop&w=600&q=85',
    a.logo_url,
    '{
      "label":"Sponsored",
      "provider":"shopee_affiliate",
      "offerId":"shopee_8821"
    }'::jsonb
FROM leo_ads.tenant t
JOIN leo_ads.advertiser a
  ON a.tenant_id = t.tenant_id
 AND a.advertiser_key = 'abc-fashion'
JOIN leo_ads.campaign c
  ON c.tenant_id = t.tenant_id
 AND c.campaign_key = 'shopee-affiliate-demo'
JOIN leo_ads.source_asset sa
  ON sa.tenant_id = t.tenant_id
 AND sa.external_asset_id = 'shopee_creative_001'
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, creative_key, version_no) DO NOTHING;


-- 7.7 Lazada JS widget
INSERT INTO leo_ads.creative (
    tenant_id,
    campaign_id,
    source_asset_id,
    creative_key,
    ad_type,
    format_code,
    render_type_code,
    status,
    priority,
    content_payload
)
SELECT
    t.tenant_id,
    c.campaign_id,
    sa.source_asset_id,
    'affiliate_lazada_js_001',
    'affiliate',
    'external_widget',
    'js_tag',
    'active',
    50,
    '{
      "provider":"lazada_affiliate",
      "publisherId":"publisher_12345",
      "campaignId":"lazada_campaign_001",
      "placementId":"sidebar_001",
      "format":"300x600"
    }'::jsonb
FROM leo_ads.tenant t
JOIN leo_ads.campaign c
  ON c.tenant_id = t.tenant_id
 AND c.campaign_key = 'lazada-affiliate-demo'
JOIN leo_ads.source_asset sa
  ON sa.tenant_id = t.tenant_id
 AND sa.external_asset_id = 'lazada_widget_001'
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, creative_key, version_no) DO NOTHING;


-- ============================================================================
-- 8. CREATIVE RENDER CONFIGURATION
-- ============================================================================

INSERT INTO leo_ads.creative_render (
    creative_id,
    render_type_code,
    template_key,
    render_config
)
SELECT
    c.creative_id,
    'native_json',
    x.template_key,
    x.render_config::jsonb
FROM leo_ads.creative c
JOIN (
    VALUES
    (
        'coolmate_retargeting_01',
        'template_single_banner_v1',
        '{"component":"single-banner","theme":"light"}'
    ),
    (
        'coolmate_product_carousel_01',
        'template_product_carousel_v1',
        '{"component":"product-carousel","theme":"light","columns":{"desktop":4,"mobile":2}}'
    ),
    (
        'coolmate_native_01',
        'template_native_card_v1',
        '{"component":"native-card","theme":"light"}'
    ),
    (
        'shopee_affiliate_001',
        'template_affiliate_product_card_v1',
        '{"component":"affiliate-product-card","theme":"light"}'
    )
) AS x(creative_key, template_key, render_config)
  ON x.creative_key = c.creative_key
WHERE c.tenant_id = (
    SELECT tenant_id
    FROM leo_ads.tenant
    WHERE tenant_key = 'demo'
)
ON CONFLICT (creative_id, render_type_code) DO NOTHING;


INSERT INTO leo_ads.creative_render (
    creative_id,
    render_type_code,
    template_key,
    loader_src,
    loader_async,
    container_id,
    container_class_name,
    render_config
)
SELECT
    c.creative_id,
    'js_tag',
    NULL,
    x.loader_src,
    TRUE,
    x.container_id,
    x.container_class_name,
    x.render_config::jsonb
FROM leo_ads.creative c
JOIN (
    VALUES
    (
        'google_ads_display_01',
        'https://securepubads.g.doubleclick.net/tag/js/gpt.js',
        'google-ad-slot-001',
        'ad-slot google-ad-slot',
        '{
          "adUnitPath":"/1234567890/example/homepage",
          "sizes":[[728,90],[970,250],[320,100]],
          "targeting":{"page":"homepage","device":"responsive"}
        }'
    ),
    (
        'google_ads_native_01',
        'https://securepubads.g.doubleclick.net/tag/js/gpt.js',
        'google-native-slot-001',
        'ad-slot google-native-slot',
        '{
          "adUnitPath":"/1234567890/example/native",
          "sizes":[[1,1]],
          "native":true
        }'
    ),
    (
        'affiliate_lazada_js_001',
        'https://affiliate.example.com/ads/lazada-widget.js',
        'affiliate-lazada-slot-001',
        'ad-slot affiliate-ad-slot',
        '{
          "publisherId":"publisher_12345",
          "campaignId":"lazada_campaign_001",
          "placementId":"sidebar_001",
          "format":"300x600"
        }'
    )
) AS x(
    creative_key,
    loader_src,
    container_id,
    container_class_name,
    render_config
)
  ON x.creative_key = c.creative_key
WHERE c.tenant_id = (
    SELECT tenant_id
    FROM leo_ads.tenant
    WHERE tenant_key = 'demo'
)
ON CONFLICT (creative_id, render_type_code) DO NOTHING;


-- ============================================================================
-- 9. DESTINATIONS
-- ============================================================================

INSERT INTO leo_ads.destination (
    creative_id,
    destination_type_code,
    url,
    final_url,
    metadata
)
SELECT
    c.creative_id,
    x.destination_type_code,
    x.url,
    x.final_url,
    x.metadata::jsonb
FROM leo_ads.creative c
JOIN (
    VALUES
    (
        'coolmate_retargeting_01',
        'url',
        'https://example.com/product/2',
        NULL,
        '{"tracking":"internal"}'
    ),
    (
        'coolmate_product_carousel_01',
        'product',
        'https://example.com/coolmate-home',
        NULL,
        '{}'
    ),
    (
        'coolmate_native_01',
        'url',
        'https://example.com/coolmate-home',
        NULL,
        '{}'
    ),
    (
        'shopee_affiliate_001',
        'affiliate_url',
        'https://affiliate.example.com/click?offer=shopee_8821',
        'https://shopee.vn/product/8821',
        '{"network":"shopee"}'
    ),
    (
        'affiliate_lazada_js_001',
        'url',
        'https://lazada.vn/',
        NULL,
        '{"network":"lazada","mode":"widget"}'
    )
) AS x(
    creative_key,
    destination_type_code,
    url,
    final_url,
    metadata
)
  ON x.creative_key = c.creative_key
WHERE c.tenant_id = (
    SELECT tenant_id
    FROM leo_ads.tenant
    WHERE tenant_key = 'demo'
)
ON CONFLICT (creative_id, destination_type_code) DO NOTHING;


-- ============================================================================
-- 10. TRACKING ENDPOINTS
-- ============================================================================

INSERT INTO leo_ads.tracking_endpoint (
    creative_id,
    event_type,
    endpoint_url,
    method,
    extra
)
SELECT
    c.creative_id,
    x.event_type,
    x.endpoint_url,
    'GET',
    x.extra::jsonb
FROM leo_ads.creative c
JOIN (
    VALUES
    (
        'coolmate_retargeting_01',
        'impression',
        'https://analytics.example.com/track/impression?source=internal',
        '{}'
    ),
    (
        'coolmate_retargeting_01',
        'click',
        'https://analytics.example.com/track/click?source=internal',
        '{}'
    ),
    (
        'coolmate_product_carousel_01',
        'impression',
        'https://analytics.example.com/track/impression?source=internal',
        '{}'
    ),
    (
        'coolmate_product_carousel_01',
        'click',
        'https://analytics.example.com/track/click?source=internal',
        '{}'
    ),
    (
        'coolmate_native_01',
        'impression',
        'https://analytics.example.com/track/impression?source=internal',
        '{}'
    ),
    (
        'coolmate_native_01',
        'click',
        'https://analytics.example.com/track/click?source=internal',
        '{}'
    ),
    (
        'google_ads_display_01',
        'impression',
        'https://analytics.example.com/track/impression?source=google',
        '{"provider":"google_ads"}'
    ),
    (
        'google_ads_display_01',
        'click',
        'https://analytics.example.com/track/click?source=google',
        '{"provider":"google_ads"}'
    ),
    (
        'google_ads_native_01',
        'impression',
        'https://analytics.example.com/track/impression?source=google',
        '{"provider":"google_ads"}'
    ),
    (
        'google_ads_native_01',
        'click',
        'https://analytics.example.com/track/click?source=google',
        '{"provider":"google_ads"}'
    ),
    (
        'shopee_affiliate_001',
        'impression',
        'https://analytics.example.com/track/impression?source=shopee',
        '{"provider":"shopee_affiliate"}'
    ),
    (
        'shopee_affiliate_001',
        'click',
        'https://analytics.example.com/track/click?source=shopee',
        '{"provider":"shopee_affiliate"}'
    ),
    (
        'affiliate_lazada_js_001',
        'impression',
        'https://analytics.example.com/track/impression?source=lazada',
        '{"provider":"lazada_affiliate"}'
    ),
    (
        'affiliate_lazada_js_001',
        'click',
        'https://analytics.example.com/track/click?source=lazada',
        '{"provider":"lazada_affiliate"}'
    )
) AS x(
    creative_key,
    event_type,
    endpoint_url,
    extra
)
  ON x.creative_key = c.creative_key
WHERE c.tenant_id = (
    SELECT tenant_id
    FROM leo_ads.tenant
    WHERE tenant_key = 'demo'
)
ON CONFLICT (creative_id, event_type) DO NOTHING;


-- ============================================================================
-- 11. CREATIVE ITEMS / PRODUCT CAROUSEL
-- ============================================================================

INSERT INTO leo_ads.creative_item (
    creative_id,
    external_item_id,
    item_type,
    item_name,
    subtitle,
    price_amount,
    currency,
    original_price_amount,
    discount_text,
    image_url,
    destination_url,
    highlight_text,
    sort_order,
    item_payload
)
SELECT
    c.creative_id,
    x.external_item_id,
    'product',
    x.item_name,
    x.subtitle,
    x.price_amount,
    'VND',
    x.original_price_amount,
    x.discount_text,
    x.image_url,
    x.destination_url,
    x.highlight_text,
    x.sort_order,
    x.item_payload::jsonb
FROM leo_ads.creative c
JOIN (
    VALUES
    (
        'coolmate_product_carousel_01',
        'p2',
        'Áo thun nam Essential',
        'Cotton mềm mại',
        249000::numeric,
        249000::numeric,
        '-9%',
        'https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?auto=format&fit=crop&w=300&q=80',
        'https://example.com/product/2',
        'Bạn vẫn quan tâm?',
        1,
        '{"category":"tshirt","recommendation":"retargeting"}'
    ),
    (
        'coolmate_product_carousel_01',
        'p3',
        'Áo polo nam Premium',
        'Phong cách hiện đại',
        399000::numeric,
        439000::numeric,
        '-9%',
        'https://images.unsplash.com/photo-1581655353564-df123a1eb820?auto=format&fit=crop&w=300&q=80',
        'https://example.com/product/3',
        NULL,
        2,
        '{"category":"polo"}'
    ),
    (
        'coolmate_product_carousel_01',
        'p4',
        'Áo thun nam Basic',
        'Thiết kế tối giản',
        199000::numeric,
        219000::numeric,
        '-9%',
        'https://images.unsplash.com/photo-1583743814966-8936f5b7be1a?auto=format&fit=crop&w=300&q=80',
        'https://example.com/product/4',
        NULL,
        3,
        '{"category":"tshirt"}'
    ),
    (
        'coolmate_product_carousel_01',
        'p5',
        'Áo thun nam Air',
        'Thoáng nhẹ cả ngày',
        299000::numeric,
        329000::numeric,
        '-9%',
        'https://images.unsplash.com/photo-1618354691373-d851c5c3a990?auto=format&fit=crop&w=300&q=80',
        'https://example.com/product/5',
        NULL,
        4,
        '{"category":"tshirt","feature":"lightweight"}'
    )
) AS x(
    creative_key,
    external_item_id,
    item_name,
    subtitle,
    price_amount,
    original_price_amount,
    discount_text,
    image_url,
    destination_url,
    highlight_text,
    sort_order,
    item_payload
)
  ON x.creative_key = c.creative_key
WHERE c.tenant_id = (
    SELECT tenant_id
    FROM leo_ads.tenant
    WHERE tenant_key = 'demo'
)
ON CONFLICT (creative_id, external_item_id) DO NOTHING;


-- Shopee single product item
INSERT INTO leo_ads.creative_item (
    creative_id,
    external_item_id,
    item_type,
    item_name,
    subtitle,
    price_amount,
    currency,
    original_price_amount,
    discount_text,
    image_url,
    destination_url,
    sort_order,
    item_payload
)
SELECT
    c.creative_id,
    'shopee_8821',
    'product',
    'Áo thun nam Premium',
    'Thời trang nam',
    189000,
    'VND',
    249000,
    '-25%',
    'https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?auto=format&fit=crop&w=600&q=85',
    'https://affiliate.example.com/click?offer=shopee_8821',
    1,
    '{"network":"shopee_affiliate","seller":"ABC Fashion"}'::jsonb
FROM leo_ads.creative c
WHERE c.creative_key = 'shopee_affiliate_001'
  AND c.tenant_id = (
      SELECT tenant_id FROM leo_ads.tenant WHERE tenant_key = 'demo'
  )
ON CONFLICT (creative_id, external_item_id) DO NOTHING;


-- ============================================================================
-- 12. AD DELIVERY OBJECTS
-- ============================================================================

INSERT INTO leo_ads.ad (
    tenant_id,
    ad_key,
    campaign_id,
    creative_id,
    placement_id,
    status,
    score_weight,
    frequency_cap,
    metadata
)
SELECT
    t.tenant_id,
    x.ad_key,
    c.campaign_id,
    cr.creative_id,
    p.placement_id,
    'active',
    x.score_weight,
    x.frequency_cap,
    x.metadata::jsonb
FROM leo_ads.tenant t
CROSS JOIN (
    VALUES
    (
        'coolmate_dynamic_retargeting_01',
        'coolmate-retargeting',
        'coolmate_retargeting_01',
        'coolmate-banner-300x250',
        100.0::real,
        5,
        '{"source":"local","originalAdId":"coolmate_dynamic_retargeting_01"}'
    ),
    (
        'coolmate_product_carousel_01',
        'coolmate-retargeting',
        'coolmate_product_carousel_01',
        'coolmate-product-carousel',
        90.0::real,
        3,
        '{"source":"local","originalAdId":"coolmate_product_carousel_01"}'
    ),
    (
        'coolmate_native_01',
        'coolmate-native-content',
        'coolmate_native_01',
        'coolmate-native',
        80.0::real,
        3,
        '{"source":"local","originalAdId":"coolmate_native_01"}'
    ),
    (
        'google_ads_display_01',
        'google-display-demo',
        'google_ads_display_01',
        'google-top-banner',
        70.0::real,
        NULL,
        '{"source":"google_ads","mode":"js_tag"}'
    ),
    (
        'google_ads_native_01',
        'google-display-demo',
        'google_ads_native_01',
        'google-native',
        65.0::real,
        NULL,
        '{"source":"google_ads","mode":"js_tag"}'
    ),
    (
        'affiliate_shopee_001',
        'shopee-affiliate-demo',
        'shopee_affiliate_001',
        'coolmate-native',
        60.0::real,
        2,
        '{"source":"shopee_affiliate","mode":"native"}'
    ),
    (
        'affiliate_lazada_js_001',
        'lazada-affiliate-demo',
        'affiliate_lazada_js_001',
        'affiliate-sidebar',
        50.0::real,
        NULL,
        '{"source":"lazada_affiliate","mode":"js_tag"}'
    )
) AS x(
    ad_key,
    campaign_key,
    creative_key,
    placement_key,
    score_weight,
    frequency_cap,
    metadata
)
JOIN leo_ads.campaign c
  ON c.tenant_id = t.tenant_id
 AND c.campaign_key = x.campaign_key
JOIN leo_ads.creative cr
  ON cr.tenant_id = t.tenant_id
 AND cr.creative_key = x.creative_key
JOIN leo_ads.placement p
  ON p.tenant_id = t.tenant_id
 AND p.placement_key = x.placement_key
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, ad_key) DO NOTHING;


-- ============================================================================
-- 13. TARGETING RULES
-- ============================================================================

-- Coolmate retargeting: VN + web/mobile
INSERT INTO leo_ads.targeting_rule (
    tenant_id,
    ad_id,
    audience_key,
    countries,
    device_types,
    languages,
    context_keywords,
    priority,
    custom_predicates
)
SELECT
    t.tenant_id,
    a.ad_id,
    'coolmate-retargeting-users',
    ARRAY['VN'],
    ARRAY['mobile','desktop'],
    ARRAY['vi'],
    ARRAY['fashion','menswear','tshirt'],
    100,
    '{
      "retargeting":true,
      "recentProductViewDays":30
    }'::jsonb
FROM leo_ads.tenant t
JOIN leo_ads.ad a
  ON a.tenant_id = t.tenant_id
 AND a.ad_key = 'coolmate_dynamic_retargeting_01'
WHERE t.tenant_key = 'demo'
AND NOT EXISTS (
    SELECT 1
    FROM leo_ads.targeting_rule tr
    WHERE tr.ad_id = a.ad_id
);


-- Coolmate carousel
INSERT INTO leo_ads.targeting_rule (
    tenant_id,
    ad_id,
    countries,
    device_types,
    languages,
    context_keywords,
    priority
)
SELECT
    t.tenant_id,
    a.ad_id,
    ARRAY['VN'],
    ARRAY['mobile','desktop'],
    ARRAY['vi'],
    ARRAY['fashion','shopping','menswear'],
    90
FROM leo_ads.tenant t
JOIN leo_ads.ad a
  ON a.tenant_id = t.tenant_id
 AND a.ad_key = 'coolmate_product_carousel_01'
WHERE t.tenant_key = 'demo'
AND NOT EXISTS (
    SELECT 1
    FROM leo_ads.targeting_rule tr
    WHERE tr.ad_id = a.ad_id
);


-- Generic Google display
INSERT INTO leo_ads.targeting_rule (
    tenant_id,
    ad_id,
    countries,
    device_types,
    priority,
    custom_predicates
)
SELECT
    t.tenant_id,
    a.ad_id,
    ARRAY['VN'],
    ARRAY['mobile','desktop','tablet'],
    70,
    '{"pageTypes":["homepage","article"]}'::jsonb
FROM leo_ads.tenant t
JOIN leo_ads.ad a
  ON a.tenant_id = t.tenant_id
 AND a.ad_key = 'google_ads_display_01'
WHERE t.tenant_key = 'demo'
AND NOT EXISTS (
    SELECT 1
    FROM leo_ads.targeting_rule tr
    WHERE tr.ad_id = a.ad_id
);


-- Affiliate product
INSERT INTO leo_ads.targeting_rule (
    tenant_id,
    ad_id,
    countries,
    device_types,
    languages,
    context_keywords,
    priority
)
SELECT
    t.tenant_id,
    a.ad_id,
    ARRAY['VN'],
    ARRAY['mobile','desktop'],
    ARRAY['vi'],
    ARRAY['fashion','shopping','discount'],
    60
FROM leo_ads.tenant t
JOIN leo_ads.ad a
  ON a.tenant_id = t.tenant_id
 AND a.ad_key = 'affiliate_shopee_001'
WHERE t.tenant_key = 'demo'
AND NOT EXISTS (
    SELECT 1
    FROM leo_ads.targeting_rule tr
    WHERE tr.ad_id = a.ad_id
);


-- ============================================================================
-- 14. AUDIENCES
-- ============================================================================

INSERT INTO leo_ads.audience (
    tenant_id,
    audience_key,
    name,
    provider,
    external_audience_id,
    membership_version,
    member_count_estimate,
    definition,
    status
)
SELECT
    t.tenant_id,
    'coolmate-retargeting-users',
    'Coolmate Recent Product Viewers',
    'internal_cdp',
    'aud_coolmate_recent_viewers',
    1,
    125000,
    '{
      "event":"product_view",
      "lookbackDays":30,
      "category":"fashion"
    }'::jsonb,
    'active'
FROM leo_ads.tenant t
WHERE t.tenant_key = 'demo'
ON CONFLICT (tenant_id, audience_key) DO NOTHING;


INSERT INTO leo_ads.ad_audience (
    ad_id,
    audience_id,
    relation_type
)
SELECT
    a.ad_id,
    au.audience_id,
    'include'
FROM leo_ads.ad a
JOIN leo_ads.audience au
  ON au.tenant_id = a.tenant_id
 AND au.audience_key = 'coolmate-retargeting-users'
WHERE a.ad_key = 'coolmate_dynamic_retargeting_01'
ON CONFLICT DO NOTHING;


-- ============================================================================
-- 15. PRECOMPUTED SERVING INDEX
--
-- This table represents what would normally be materialized into Redis.
-- ============================================================================

INSERT INTO leo_ads.placement_ad (
    placement_id,
    ad_id,
    rank_score,
    valid_from,
    valid_to,
    is_active
)
SELECT
    a.placement_id,
    a.ad_id,
    a.score_weight,
    now(),
    now() + interval '30 days',
    TRUE
FROM leo_ads.ad a
WHERE a.tenant_id = (
    SELECT tenant_id
    FROM leo_ads.tenant
    WHERE tenant_key = 'demo'
)
AND a.status = 'active'
ON CONFLICT (placement_id, ad_id) DO UPDATE
SET
    rank_score = EXCLUDED.rank_score,
    valid_from = EXCLUDED.valid_from,
    valid_to = EXCLUDED.valid_to,
    is_active = EXCLUDED.is_active;


-- ============================================================================
-- 16. OPTIONAL DEMO USER SERVING KEYS
--
-- These are intentionally synthetic. No real PII.
-- ============================================================================

INSERT INTO leo_ads.user_serving_key (
    user_key_hash,
    tenant_id,
    profile_ref,
    first_seen_at,
    last_seen_at,
    attributes
)
SELECT
    decode(md5(x.profile_ref), 'hex'),
    t.tenant_id,
    x.profile_ref,
    now() - interval '10 days',
    now(),
    x.attributes::jsonb
FROM leo_ads.tenant t
CROSS JOIN (
    VALUES
    (
        'demo-user-001',
        '{"device":"mobile","country":"VN","language":"vi","segment":"fashion"}'
    ),
    (
        'demo-user-002',
        '{"device":"desktop","country":"VN","language":"vi","segment":"shopping"}'
    ),
    (
        'demo-user-003',
        '{"device":"mobile","country":"VN","language":"vi","segment":"new-user"}'
    )
) AS x(profile_ref, attributes)
WHERE t.tenant_key = 'demo'
ON CONFLICT (user_key_hash) DO NOTHING;


-- ============================================================================
-- 17. DEMO EVENT DATA
--
-- Only a few events are inserted to verify the event schema.
-- Production traffic should normally enter through Kafka.
-- ============================================================================

INSERT INTO leo_ads.ad_event (
    event_time,
    tenant_id,
    event_type,
    ad_id,
    campaign_id,
    creative_id,
    placement_id,
    user_key_hash,
    request_id,
    session_id,
    device_type,
    country_code,
    page_url,
    revenue_amount,
    currency,
    payload
)
SELECT
    now() - interval '5 minutes',
    t.tenant_id,
    'impression',
    a.ad_id,
    a.campaign_id,
    a.creative_id,
    a.placement_id,
    decode(md5('demo-user-001'), 'hex'),
    gen_random_uuid(),
    gen_random_uuid(),
    'mobile',
    'VN',
    'https://example.com/home',
    0.0,
    'VND',
    '{"demo":true,"source":"sample-data-init"}'::jsonb
FROM leo_ads.tenant t
JOIN leo_ads.ad a
  ON a.tenant_id = t.tenant_id
 AND a.ad_key = 'coolmate_dynamic_retargeting_01'
WHERE t.tenant_key = 'demo';


INSERT INTO leo_ads.ad_event (
    event_time,
    tenant_id,
    event_type,
    ad_id,
    campaign_id,
    creative_id,
    placement_id,
    user_key_hash,
    request_id,
    session_id,
    device_type,
    country_code,
    page_url,
    revenue_amount,
    currency,
    payload
)
SELECT
    now() - interval '2 minutes',
    t.tenant_id,
    'click',
    a.ad_id,
    a.campaign_id,
    a.creative_id,
    a.placement_id,
    decode(md5('demo-user-001'), 'hex'),
    gen_random_uuid(),
    gen_random_uuid(),
    'mobile',
    'VN',
    'https://example.com/product/2',
    250.0,
    'VND',
    '{"demo":true,"source":"sample-data-init"}'::jsonb
FROM leo_ads.tenant t
JOIN leo_ads.ad a
  ON a.tenant_id = t.tenant_id
 AND a.ad_key = 'coolmate_dynamic_retargeting_01'
WHERE t.tenant_key = 'demo';


-- ============================================================================
-- 18. VERIFICATION
-- ============================================================================

DO $$
DECLARE
    v_tenant_count BIGINT;
    v_ad_count BIGINT;
    v_creative_count BIGINT;
    v_placement_count BIGINT;
    v_serving_count BIGINT;
BEGIN
    SELECT COUNT(*)
      INTO v_tenant_count
      FROM leo_ads.tenant
     WHERE tenant_key = 'demo';

    SELECT COUNT(*)
      INTO v_ad_count
      FROM leo_ads.ad
     WHERE tenant_id = (
         SELECT tenant_id
         FROM leo_ads.tenant
         WHERE tenant_key = 'demo'
     );

    SELECT COUNT(*)
      INTO v_creative_count
      FROM leo_ads.creative
     WHERE tenant_id = (
         SELECT tenant_id
         FROM leo_ads.tenant
         WHERE tenant_key = 'demo'
     );

    SELECT COUNT(*)
      INTO v_placement_count
      FROM leo_ads.placement
     WHERE tenant_id = (
         SELECT tenant_id
         FROM leo_ads.tenant
         WHERE tenant_key = 'demo'
     );

    SELECT COUNT(*)
      INTO v_serving_count
      FROM leo_ads.placement_ad pa
      JOIN leo_ads.ad a
        ON a.ad_id = pa.ad_id
     WHERE a.tenant_id = (
         SELECT tenant_id
         FROM leo_ads.tenant
         WHERE tenant_key = 'demo'
     );

    RAISE NOTICE 'Demo tenant count      : %', v_tenant_count;
    RAISE NOTICE 'Demo placement count   : %', v_placement_count;
    RAISE NOTICE 'Demo creative count    : %', v_creative_count;
    RAISE NOTICE 'Demo ad count          : %', v_ad_count;
    RAISE NOTICE 'Serving-index entries  : %', v_serving_count;
END $$;


COMMIT;


-- ============================================================================
-- QUICK VERIFICATION QUERIES
-- ============================================================================
--
-- SELECT
--     p.placement_key,
--     a.ad_key,
--     a.score_weight,
--     c.format_code,
--     c.render_type_code,
--     c.headline
-- FROM leo_ads.placement_ad pa
-- JOIN leo_ads.placement p ON p.placement_id = pa.placement_id
-- JOIN leo_ads.ad a ON a.ad_id = pa.ad_id
-- JOIN leo_ads.creative c ON c.creative_id = a.creative_id
-- WHERE pa.is_active = TRUE
-- ORDER BY p.placement_key, pa.rank_score DESC;
--
-- SELECT
--     c.creative_key,
--     c.format_code,
--     c.render_type_code,
--     cr.template_key,
--     cr.loader_src
-- FROM leo_ads.creative c
-- LEFT JOIN leo_ads.creative_render cr
--   ON cr.creative_id = c.creative_id
-- ORDER BY c.creative_key;
--
-- SELECT
--     a.ad_key,
--     tr.audience_key,
--     tr.countries,
--     tr.device_types,
--     tr.context_keywords
-- FROM leo_ads.ad a
-- JOIN leo_ads.targeting_rule tr ON tr.ad_id = a.ad_id
-- ORDER BY a.ad_key;
--
-- ============================================================================
