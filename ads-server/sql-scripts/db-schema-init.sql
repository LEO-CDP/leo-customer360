-- ============================================================================
-- LEO ADS / HIGH-SCALE AD SERVER SCHEMA
-- PostgreSQL 16+
--
-- Goals:
--   1. Support ~40M users/profiles without putting user-level state in the
--      creative catalog.
--   2. Support local ads, Google Ad Manager, affiliate networks, JS widgets,
--      native JSON, display, carousel, video, etc.
--   3. Keep the ad-serving hot path relational and index-friendly.
--   4. Keep provider-specific payload/config flexible in JSONB.
--   5. Separate control plane (campaign/creative/config) from serving
--      eligibility and event telemetry.
--
-- Recommended runtime architecture:
--   PostgreSQL  -> source of truth / control plane
--   Redis       -> hot cache of active placements + candidate ads
--   Targeting   -> precomputed segment membership / feature store
--   Ad API      -> low-latency decisioning
--   Kafka       -> impression/click/conversion/event stream
--
-- NOTE:
--   Do NOT store 40M-user impression/click/event traffic in the core catalog
--   tables. Use append-only partitioned event tables or a streaming sink.
-- ============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS leo_ads;

-- ----------------------------------------------------------------------------
-- 1. TENANCY
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leo_ads.tenant (
    tenant_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_key       VARCHAR(100) NOT NULL UNIQUE,
    name             VARCHAR(255) NOT NULL,
    status            VARCHAR(20) NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'paused', 'archived')),
    settings         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 2. COMMON DICTIONARIES
-- Avoid PostgreSQL ENUM for values that external providers may introduce.
-- This makes new source/format types deployable without a DB type migration.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leo_ads.source_type (
    source_type_code VARCHAR(40) PRIMARY KEY
);

INSERT INTO leo_ads.source_type (source_type_code) VALUES
    ('local'),
    ('ad_network'),
    ('affiliate')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS leo_ads.render_type (
    render_type_code VARCHAR(40) PRIMARY KEY
);

INSERT INTO leo_ads.render_type (render_type_code) VALUES
    ('native_json'),
    ('js_tag'),
    ('iframe'),
    ('html'),
    ('video'),
    ('redirect')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS leo_ads.destination_type (
    destination_type_code VARCHAR(40) PRIMARY KEY
);

INSERT INTO leo_ads.destination_type (destination_type_code) VALUES
    ('url'),
    ('product'),
    ('affiliate_url'),
    ('app_deep_link'),
    ('custom')
ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------------------
-- 3. ADVERTISER / SOURCE ACCOUNTS
-- One advertiser may have multiple provider accounts.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leo_ads.advertiser (
    advertiser_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id           BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    advertiser_key      VARCHAR(200) NOT NULL,
    name                VARCHAR(255) NOT NULL,
    description         TEXT,
    title               VARCHAR(255),
    logo_url            TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, advertiser_key)
);

CREATE TABLE IF NOT EXISTS leo_ads.source_account (
    source_account_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id           BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    advertiser_id       BIGINT REFERENCES leo_ads.advertiser(advertiser_id)
                        ON DELETE SET NULL,
    source_type_code    VARCHAR(40) NOT NULL REFERENCES leo_ads.source_type(source_type_code),
    provider_key        VARCHAR(100) NOT NULL,
    network_key         VARCHAR(100),
    account_key         VARCHAR(200),
    publisher_id        VARCHAR(200),
    api_account_ref     VARCHAR(255),
    status              VARCHAR(20) NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'paused', 'revoked')),
    config              JSONB NOT NULL DEFAULT '{}'::jsonb,
    secret_ref          VARCHAR(255),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, provider_key, account_key)
);

-- External source objects: Google campaign, Shopee offer, Lazada widget, etc.
CREATE TABLE IF NOT EXISTS leo_ads.source_asset (
    source_asset_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id           BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    source_account_id   BIGINT REFERENCES leo_ads.source_account(source_account_id)
                        ON DELETE SET NULL,
    asset_type          VARCHAR(60) NOT NULL,
    external_asset_id   VARCHAR(255) NOT NULL,
    campaign_external_id VARCHAR(255),
    creative_external_id VARCHAR(255),
    ad_unit_external_id VARCHAR(255),
    raw_payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status              VARCHAR(20) NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'paused', 'archived')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, source_account_id, asset_type, external_asset_id)
);

-- ----------------------------------------------------------------------------
-- 4. PLACEMENTS
-- A placement is a stable publisher inventory slot.
-- Formats are capabilities of the slot, not a property of one ad forever.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leo_ads.placement (
    placement_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id           BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    placement_key       VARCHAR(120) NOT NULL,
    name                 VARCHAR(255),
    status               VARCHAR(20) NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'paused', 'archived')),
    min_width_px         INTEGER CHECK (min_width_px IS NULL OR min_width_px >= 0),
    max_width_px         INTEGER CHECK (max_width_px IS NULL OR max_width_px >= 0),
    min_height_px        INTEGER CHECK (min_height_px IS NULL OR min_height_px >= 0),
    max_height_px        INTEGER CHECK (max_height_px IS NULL OR max_height_px >= 0),
    responsive           BOOLEAN NOT NULL DEFAULT FALSE,
    metadata             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, placement_key)
);

CREATE TABLE IF NOT EXISTS leo_ads.placement_format (
    placement_id         BIGINT NOT NULL REFERENCES leo_ads.placement(placement_id)
                         ON DELETE CASCADE,
    format_code          VARCHAR(80) NOT NULL,
    width_px              INTEGER,
    height_px             INTEGER,
    width_unit            VARCHAR(8) NOT NULL DEFAULT 'px'
                         CHECK (width_unit IN ('px', '%', 'auto')),
    height_unit           VARCHAR(8) NOT NULL DEFAULT 'px'
                         CHECK (height_unit IN ('px', '%', 'auto')),
    responsive            BOOLEAN NOT NULL DEFAULT FALSE,
    constraints           JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (placement_id, format_code),
    CHECK (width_px IS NULL OR width_px >= 0),
    CHECK (height_px IS NULL OR height_px >= 0)
);

-- ----------------------------------------------------------------------------
-- 5. CAMPAIGN
-- Campaign is the buying/business object; ad is the delivery object.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leo_ads.campaign (
    campaign_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id            BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    advertiser_id        BIGINT REFERENCES leo_ads.advertiser(advertiser_id)
                         ON DELETE SET NULL,
    source_account_id    BIGINT REFERENCES leo_ads.source_account(source_account_id)
                         ON DELETE SET NULL,
    campaign_key         VARCHAR(200) NOT NULL,
    name                  VARCHAR(255) NOT NULL,
    objective             VARCHAR(60),
    buying_model          VARCHAR(40),
    budget_amount         NUMERIC(20,6),
    currency              CHAR(3),
    daily_budget_amount  NUMERIC(20,6),
    status               VARCHAR(20) NOT NULL DEFAULT 'draft'
                         CHECK (status IN ('draft', 'active', 'paused', 'completed', 'archived')),
    starts_at             TIMESTAMPTZ,
    ends_at               TIMESTAMPTZ,
    metadata              JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, campaign_key),
    CHECK (ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at)
);

-- ----------------------------------------------------------------------------
-- 6. CREATIVE / FORMAT
-- One ad can have many creative variants. A creative is format-neutral at
-- the relational level and carries flexible provider/render payload.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leo_ads.creative (
    creative_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id             BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    campaign_id           BIGINT REFERENCES leo_ads.campaign(campaign_id)
                          ON DELETE SET NULL,
    advertiser_id         BIGINT REFERENCES leo_ads.advertiser(advertiser_id)
                          ON DELETE SET NULL,
    source_asset_id       BIGINT REFERENCES leo_ads.source_asset(source_asset_id)
                          ON DELETE SET NULL,
    creative_key          VARCHAR(200) NOT NULL,
    ad_type               VARCHAR(80) NOT NULL,
    format_code           VARCHAR(80) NOT NULL,
    render_type_code      VARCHAR(40) REFERENCES leo_ads.render_type(render_type_code),
    status                VARCHAR(20) NOT NULL DEFAULT 'active'
                          CHECK (status IN ('draft', 'active', 'paused', 'archived')),
    version_no            INTEGER NOT NULL DEFAULT 1 CHECK (version_no > 0),
    priority              INTEGER NOT NULL DEFAULT 0,
    starts_at             TIMESTAMPTZ,
    ends_at               TIMESTAMPTZ,

    -- Hot/common creative fields
    headline              TEXT,
    subheadline           TEXT,
    body                  TEXT,
    cta                   VARCHAR(255),
    image_url             TEXT,
    video_url             TEXT,
    logo_url              TEXT,

    -- Flexible fields for source/provider/template-specific content.
    -- Examples: price, badge, product metadata, native fields, etc.
    content_payload       JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (tenant_id, creative_key, version_no),
    CHECK (ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at)
);

-- Creative payload for renderer. Kept separate so changing template/config does
-- not require modifying the canonical creative record.
CREATE TABLE IF NOT EXISTS leo_ads.creative_render (
    creative_render_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    creative_id           BIGINT NOT NULL REFERENCES leo_ads.creative(creative_id)
                          ON DELETE CASCADE,
    render_type_code      VARCHAR(40) NOT NULL REFERENCES leo_ads.render_type(render_type_code),
    template_key          VARCHAR(120),
    loader_src            TEXT,
    loader_async          BOOLEAN NOT NULL DEFAULT TRUE,
    container_id          VARCHAR(255),
    container_class_name  VARCHAR(255),
    render_config         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (creative_id, render_type_code)
);

-- ----------------------------------------------------------------------------
-- 7. DESTINATION / TRACKING
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leo_ads.destination (
    destination_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    creative_id           BIGINT NOT NULL REFERENCES leo_ads.creative(creative_id)
                          ON DELETE CASCADE,
    destination_type_code VARCHAR(40) NOT NULL REFERENCES leo_ads.destination_type(destination_type_code),
    url                   TEXT,
    final_url             TEXT,
    metadata              JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (creative_id, destination_type_code),
    CHECK (url IS NOT NULL OR final_url IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS leo_ads.tracking_endpoint (
    tracking_endpoint_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    creative_id           BIGINT NOT NULL REFERENCES leo_ads.creative(creative_id)
                          ON DELETE CASCADE,
    event_type             VARCHAR(40) NOT NULL
                           CHECK (event_type IN (
                               'impression',
                               'click',
                               'viewable_impression',
                               'conversion',
                               'video_start',
                               'video_complete'
                           )),
    endpoint_url           TEXT NOT NULL,
    method                 VARCHAR(10) NOT NULL DEFAULT 'GET',
    extra                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (creative_id, event_type)
);

-- ----------------------------------------------------------------------------
-- 8. CREATIVE ITEMS
-- Supports carousel, product grids, recommendation cards, etc.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leo_ads.creative_item (
    creative_item_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    creative_id            BIGINT NOT NULL REFERENCES leo_ads.creative(creative_id)
                           ON DELETE CASCADE,
    external_item_id       VARCHAR(255) NOT NULL,
    item_type              VARCHAR(60) NOT NULL DEFAULT 'product',
    item_name              VARCHAR(500),
    subtitle               TEXT,
    price_amount           NUMERIC(20,6),
    currency               CHAR(3),
    original_price_amount  NUMERIC(20,6),
    discount_text          VARCHAR(80),
    image_url              TEXT,
    destination_url        TEXT,
    highlight_text         TEXT,
    sort_order             SMALLINT NOT NULL DEFAULT 0,
    item_payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (creative_id, external_item_id)
);

-- ----------------------------------------------------------------------------
-- 9. AD DELIVERY OBJECT
-- Stable object returned by the Ad API. This is intentionally thin.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leo_ads.ad (
    ad_id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id              BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    ad_key                 VARCHAR(200) NOT NULL,
    campaign_id            BIGINT REFERENCES leo_ads.campaign(campaign_id)
                           ON DELETE SET NULL,
    creative_id            BIGINT NOT NULL REFERENCES leo_ads.creative(creative_id)
                           ON DELETE RESTRICT,
    placement_id           BIGINT NOT NULL REFERENCES leo_ads.placement(placement_id)
                           ON DELETE RESTRICT,
    status                 VARCHAR(20) NOT NULL DEFAULT 'active'
                           CHECK (status IN ('draft', 'active', 'paused', 'archived')),
    score_weight           REAL NOT NULL DEFAULT 1.0,
    frequency_cap          INTEGER CHECK (frequency_cap IS NULL OR frequency_cap >= 0),
    metadata               JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, ad_key)
);

-- ----------------------------------------------------------------------------
-- 10. TARGETING / ELIGIBILITY
--
-- Targeting rules are campaign/ad level. User membership should live outside
-- this catalog in an audience/feature system. This table stores references
-- to audience IDs and cheap request-time predicates.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leo_ads.targeting_rule (
    targeting_rule_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id              BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    ad_id                  BIGINT NOT NULL REFERENCES leo_ads.ad(ad_id)
                           ON DELETE CASCADE,
    audience_key           VARCHAR(200),
    countries              TEXT[],
    regions                TEXT[],
    device_types           TEXT[],
    os_types               TEXT[],
    browser_types          TEXT[],
    languages              TEXT[],
    min_age                SMALLINT,
    max_age                SMALLINT,
    gender_codes           TEXT[],
    context_keywords       TEXT[],
    custom_predicates      JSONB NOT NULL DEFAULT '{}'::jsonb,
    exclude_predicates     JSONB NOT NULL DEFAULT '{}'::jsonb,
    priority                INTEGER NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Optional precomputed audience reference. Do not materialize 40M membership
-- rows here unless you have a specific serving requirement.
CREATE TABLE IF NOT EXISTS leo_ads.audience (
    audience_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id               BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    audience_key            VARCHAR(200) NOT NULL,
    name                    VARCHAR(255) NOT NULL,
    provider                 VARCHAR(100),
    external_audience_id    VARCHAR(255),
    membership_version      BIGINT NOT NULL DEFAULT 1,
    member_count_estimate   BIGINT,
    definition              JSONB NOT NULL DEFAULT '{}'::jsonb,
    status                  VARCHAR(20) NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'paused', 'archived')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, audience_key)
);

CREATE TABLE IF NOT EXISTS leo_ads.ad_audience (
    ad_id                   BIGINT NOT NULL REFERENCES leo_ads.ad(ad_id)
                            ON DELETE CASCADE,
    audience_id             BIGINT NOT NULL REFERENCES leo_ads.audience(audience_id)
                            ON DELETE CASCADE,
    relation_type            VARCHAR(10) NOT NULL
                            CHECK (relation_type IN ('include', 'exclude')),
    PRIMARY KEY (ad_id, audience_id, relation_type)
);

-- ----------------------------------------------------------------------------
-- 11. PRECOMPUTED SERVING INDEX
--
-- Optional materialized candidate list. This is what Redis can cache.
-- It avoids scanning the full ads table for every request.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leo_ads.placement_ad (
    placement_id             BIGINT NOT NULL REFERENCES leo_ads.placement(placement_id)
                             ON DELETE CASCADE,
    ad_id                    BIGINT NOT NULL REFERENCES leo_ads.ad(ad_id)
                             ON DELETE CASCADE,
    rank_score               REAL NOT NULL DEFAULT 0,
    valid_from               TIMESTAMPTZ,
    valid_to                 TIMESTAMPTZ,
    is_active                BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (placement_id, ad_id),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

-- ----------------------------------------------------------------------------
-- 12. OPTIONAL USER/PROFILE SERVING KEYS
--
-- Store identifiers/hashes used by ad serving, not the entire customer 360.
-- This is intentionally a compact serving table. Full customer profiles belong
-- to the CDP / profile service.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leo_ads.user_serving_key (
    user_key_hash            BYTEA PRIMARY KEY,
    tenant_id                BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    profile_ref               VARCHAR(255),
    first_seen_at             TIMESTAMPTZ,
    last_seen_at              TIMESTAMPTZ,
    attributes                JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 13. PARTITIONED IMPRESSION / CLICK EVENT STREAM
-- Operational ad catalog should not be polluted by telemetry.
--
-- Use Kafka as primary ingestion for very high volume. PostgreSQL can retain a
-- recent operational window or serve as an analytics landing zone.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leo_ads.ad_event (
    event_id                 UUID NOT NULL DEFAULT gen_random_uuid(),
    event_time               TIMESTAMPTZ NOT NULL,
    tenant_id                BIGINT NOT NULL,
    event_type               VARCHAR(40) NOT NULL,
    ad_id                    BIGINT,
    campaign_id              BIGINT,
    creative_id              BIGINT,
    placement_id             BIGINT,
    user_key_hash            BYTEA,
    request_id               UUID,
    session_id               UUID,
    device_type              VARCHAR(30),
    country_code              CHAR(2),
    page_url                 TEXT,
    revenue_amount           NUMERIC(20,8),
    currency                 CHAR(3),
    payload                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (event_time, event_id)
) PARTITION BY RANGE (event_time);

-- Initial rolling partitions. Production should create future partitions with
-- a scheduled job.
CREATE TABLE IF NOT EXISTS leo_ads.ad_event_2026_08
    PARTITION OF leo_ads.ad_event
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

CREATE TABLE IF NOT EXISTS leo_ads.ad_event_default
    PARTITION OF leo_ads.ad_event DEFAULT;

-- ----------------------------------------------------------------------------
-- 14. INDEXES
-- ----------------------------------------------------------------------------

-- Tenant isolation / common admin access
CREATE INDEX IF NOT EXISTS idx_advertiser_tenant
    ON leo_ads.advertiser (tenant_id, advertiser_id);

CREATE INDEX IF NOT EXISTS idx_campaign_tenant_status
    ON leo_ads.campaign (tenant_id, status, campaign_id);

CREATE INDEX IF NOT EXISTS idx_source_account_tenant_provider
    ON leo_ads.source_account (tenant_id, provider_key, status);

CREATE INDEX IF NOT EXISTS idx_source_asset_external
    ON leo_ads.source_asset (tenant_id, source_account_id, external_asset_id);

-- Hot ad-serving path
CREATE INDEX IF NOT EXISTS idx_placement_active
    ON leo_ads.placement (tenant_id, placement_key)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_ad_active_by_placement
    ON leo_ads.ad (tenant_id, placement_id, status, ad_id)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_placement_ad_hot
    ON leo_ads.placement_ad (placement_id, is_active, rank_score DESC, ad_id);

CREATE INDEX IF NOT EXISTS idx_ad_campaign_active
    ON leo_ads.ad (campaign_id, status)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_creative_active
    ON leo_ads.creative (tenant_id, creative_id, status)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_creative_source_asset
    ON leo_ads.creative (source_asset_id);

-- Targeting lookup
CREATE INDEX IF NOT EXISTS idx_targeting_ad
    ON leo_ads.targeting_rule (ad_id, priority DESC);

CREATE INDEX IF NOT EXISTS idx_ad_audience_audience
    ON leo_ads.ad_audience (audience_id, relation_type, ad_id);

-- Event analytics
CREATE INDEX IF NOT EXISTS idx_ad_event_tenant_time
    ON leo_ads.ad_event (tenant_id, event_time DESC);

CREATE INDEX IF NOT EXISTS idx_ad_event_ad_time
    ON leo_ads.ad_event (ad_id, event_time DESC);

CREATE INDEX IF NOT EXISTS idx_ad_event_request
    ON leo_ads.ad_event (request_id);

-- JSONB only where flexible provider data is genuinely queried.
CREATE INDEX IF NOT EXISTS idx_source_asset_payload_gin
    ON leo_ads.source_asset USING GIN (raw_payload);

CREATE INDEX IF NOT EXISTS idx_creative_payload_gin
    ON leo_ads.creative USING GIN (content_payload);

-- ----------------------------------------------------------------------------
-- 15. UPDATED_AT TRIGGER
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION leo_ads.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'tenant',
        'advertiser',
        'source_account',
        'source_asset',
        'placement',
        'campaign',
        'creative',
        'creative_render',
        'destination',
        'tracking_endpoint',
        'creative_item',
        'ad',
        'targeting_rule',
        'audience',
        'user_serving_key'
    ]
    LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS trg_%I_updated_at ON leo_ads.%I',
            t, t
        );
        EXECUTE format(
            'CREATE TRIGGER trg_%I_updated_at
             BEFORE UPDATE ON leo_ads.%I
             FOR EACH ROW EXECUTE FUNCTION leo_ads.set_updated_at()',
            t, t
        );
    END LOOP;
END $$;

-- ----------------------------------------------------------------------------
-- 16. OPTIONAL VIEW FOR ADMIN / DEBUGGING
-- Keep the serving API on indexed base tables or Redis; this view is not the
-- primary hot path.
-- ----------------------------------------------------------------------------

CREATE OR REPLACE VIEW leo_ads.v_active_ads AS
SELECT
    a.ad_id,
    a.tenant_id,
    a.ad_key,
    a.placement_id,
    a.status AS ad_status,
    a.score_weight,
    c.creative_id,
    c.creative_key,
    c.ad_type,
    c.format_code,
    c.render_type_code,
    c.headline,
    c.subheadline,
    c.body,
    c.cta,
    c.image_url,
    c.video_url,
    c.logo_url,
    c.content_payload,
    ca.campaign_id,
    ca.campaign_key,
    ca.objective
FROM leo_ads.ad a
JOIN leo_ads.creative c
  ON c.creative_id = a.creative_id
LEFT JOIN leo_ads.campaign ca
  ON ca.campaign_id = a.campaign_id
WHERE a.status = 'active'
  AND c.status = 'active';

COMMIT;
