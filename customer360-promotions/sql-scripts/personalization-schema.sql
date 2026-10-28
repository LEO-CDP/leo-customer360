-- ============================================================================
-- c360 PROMOTIONS / AD PERSONALISATION SCHEMA ADDITIONS
-- PostgreSQL 16+
--
-- Applies AFTER db-schema-init.sql and BEFORE sample-data-init.sql.
-- Idempotent: safe to run on every start / deploy (IF NOT EXISTS and guarded
-- DO blocks, same convention as db-schema-init.sql).
--
-- Design: specs/003-ad-personalization-engine/data-model.md
--
-- What this adds
--   1. A tenant link and event-quality columns on existing tables.
--   2. personalization_setting, consent_record, consent_history.
--   3. visitor_behaviour_profile, ad_stat_hourly, cohort_pattern.
--   4. serving_decision (daily partitions) and erasure_request.
--
-- Isolation: leo_ads has no row-level security, so every table below leads
-- its key with tenant_id and every access path must filter by it.
-- ============================================================================

BEGIN;

-- Partition bounds are written as dates and interpreted in the session time
-- zone, so pin it: the same bounds on every environment.
SET LOCAL TIME ZONE 'UTC';

CREATE SCHEMA IF NOT EXISTS leo_ads;

-- ----------------------------------------------------------------------------
-- PART 1. TENANT LINK AND EVENT TABLE ADDITIONS
-- ----------------------------------------------------------------------------

-- Link to the platform tenant (customer360.sys_tenant.tenant_id) so the admin
-- plane can resolve the ad tenant from the caller's token only. Nullable and
-- unique where set: not every ad tenant has to be linked.
ALTER TABLE leo_ads.tenant
    ADD COLUMN IF NOT EXISTS customer360_tenant_id UUID;

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_customer360_tenant_id
    ON leo_ads.tenant (customer360_tenant_id)
    WHERE customer360_tenant_id IS NOT NULL;

-- Quality markers (for example 'bot', 'duplicate', 'orphan_click'). Events
-- carrying any flag are excluded from profiles and statistics.
ALTER TABLE leo_ads.ad_event
    ADD COLUMN IF NOT EXISTS flags TEXT[] NOT NULL DEFAULT '{}'::text[];

-- Closed set of event types. 'view_through_conversion' is already used by the
-- demo seed data and is counted as a conversion in statistics.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ad_event_event_type_check'
          AND conrelid = 'leo_ads.ad_event'::regclass
    ) THEN
        ALTER TABLE leo_ads.ad_event
            ADD CONSTRAINT ad_event_event_type_check
            CHECK (event_type IN (
                'impression',
                'view',
                'click',
                'dismiss',
                'conversion',
                'view_through_conversion'
            ));
    END IF;
END
$$;

-- Per-visitor lookups: profile rebuilds and erasure. Partial, because events
-- from visitors who are not eligible for profiling carry no key. (Per-ad
-- lookups are already served by idx_ad_event_ad_time in db-schema-init.sql.)
CREATE INDEX IF NOT EXISTS idx_ad_event_tenant_user_time
    ON leo_ads.ad_event (tenant_id, user_key_hash, event_time DESC)
    WHERE user_key_hash IS NOT NULL;

-- Conversion attribution: find the click a postback refers to by its click id.
CREATE INDEX IF NOT EXISTS idx_ad_event_click_id
    ON leo_ads.ad_event (tenant_id, (payload ->> 'click_id'))
    WHERE event_type = 'click';

-- Monthly partitions for the current and next two months. If rows for a month
-- already landed in the default partition (for example demo data), Postgres
-- refuses the new partition; that is reported as a NOTICE and the
-- partition_maintenance Dagster job moves those rows and attaches the
-- partition instead.
DO $$
DECLARE
    month_start DATE;
    month_end   DATE;
    part_name   TEXT;
    i           INTEGER;
BEGIN
    FOR i IN 0..2 LOOP
        month_start := (date_trunc('month', now()) + (i || ' month')::interval)::date;
        month_end   := (month_start + interval '1 month')::date;
        part_name   := 'ad_event_' || to_char(month_start, 'YYYY_MM');
        BEGIN
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS leo_ads.%I PARTITION OF leo_ads.ad_event '
                'FOR VALUES FROM (%L) TO (%L)',
                part_name, month_start, month_end
            );
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Skipped partition leo_ads.%: % (partition_maintenance will fix up)',
                part_name, SQLERRM;
        END;
    END LOOP;
END
$$;

-- ----------------------------------------------------------------------------
-- PART 2. SETTINGS AND CONSENT
-- ----------------------------------------------------------------------------

-- On/off switch, hold-out share and consent mode, scoped to a tenant, a site
-- or a placement. The most specific row wins (placement, then site, then
-- tenant). Personalisation ships OFF: enabled defaults to FALSE.
CREATE TABLE IF NOT EXISTS leo_ads.personalization_setting (
    setting_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id       BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    scope_type      VARCHAR(20) NOT NULL
                    CHECK (scope_type IN ('tenant', 'site', 'placement')),
    -- '' for tenant scope; the opaque site_key for site scope; the
    -- placement_key for placement scope.
    scope_ref       TEXT NOT NULL DEFAULT '',
    enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    -- Share of visitors served default ranking only. Valid range 0 to 100.
    holdout_pct     NUMERIC(5,2) NOT NULL DEFAULT 10.00
                    CHECK (holdout_pct >= 0 AND holdout_pct <= 100),
    -- Measurement-period salt for deterministic hold-out assignment. Changing
    -- it starts a new measurement period.
    holdout_salt    TEXT NOT NULL DEFAULT 'default',
    -- Only meaningful on the tenant-scope row; ignored on other scopes.
    consent_mode    VARCHAR(10) NOT NULL DEFAULT 'opt_in'
                    CHECK (consent_mode IN ('opt_in', 'opt_out')),
    updated_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, scope_type, scope_ref),
    CHECK (scope_type <> 'tenant' OR scope_ref = '')
);

-- A visitor's current consent state for behaviour-based personalisation.
-- No row means "no recorded choice": the tenant's consent mode decides.
CREATE TABLE IF NOT EXISTS leo_ads.consent_record (
    tenant_id       BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    user_key_hash   BYTEA NOT NULL,
    status          VARCHAR(10) NOT NULL
                    CHECK (status IN ('granted', 'denied')),
    source          VARCHAR(40) NOT NULL DEFAULT 'widget',
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_key_hash)
);

-- Append-only audit of every consent change ("including when it changed").
CREATE TABLE IF NOT EXISTS leo_ads.consent_history (
    history_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id       BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    user_key_hash   BYTEA NOT NULL,
    status          VARCHAR(10) NOT NULL
                    CHECK (status IN ('granted', 'denied')),
    source          VARCHAR(40) NOT NULL DEFAULT 'widget',
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_consent_history_tenant_key
    ON leo_ads.consent_history (tenant_id, user_key_hash, changed_at DESC);

-- ----------------------------------------------------------------------------
-- PART 3. PROFILES, STATISTICS AND COHORT PATTERNS
-- ----------------------------------------------------------------------------

-- Per-visitor summary of ad behaviour. Durable and rebuildable from ad_event;
-- Redis holds the fresh deltas. Never written for a visitor who is not
-- eligible under their consent state.
CREATE TABLE IF NOT EXISTS leo_ads.visitor_behaviour_profile (
    tenant_id        BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    user_key_hash    BYTEA NOT NULL,
    -- {dimension: {value: {"score": float, "n": int, "last_at": iso8601}}}
    affinities       JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- {ad_id: iso8601 time of the dismissal}
    recent_dismissed JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_count      INTEGER NOT NULL DEFAULT 0 CHECK (event_count >= 0),
    last_event_at    TIMESTAMPTZ,
    profile_version  INTEGER NOT NULL DEFAULT 1,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_key_hash)
);

-- Hourly counts of unflagged events per ad and group, used for objective-rate
-- priors, placement baselines and the personalised-versus-hold-out report.
-- No foreign key to ad: statistics outlive archived or removed ads.
CREATE TABLE IF NOT EXISTS leo_ads.ad_stat_hourly (
    tenant_id        BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    ad_id            BIGINT NOT NULL,
    hour_start       TIMESTAMPTZ NOT NULL,
    group_code       VARCHAR(20) NOT NULL
                     CHECK (group_code IN ('personalised', 'holdout', 'default')),
    placement_id     BIGINT,
    campaign_id      BIGINT,
    impressions      BIGINT NOT NULL DEFAULT 0 CHECK (impressions >= 0),
    views            BIGINT NOT NULL DEFAULT 0 CHECK (views >= 0),
    clicks           BIGINT NOT NULL DEFAULT 0 CHECK (clicks >= 0),
    conversions      BIGINT NOT NULL DEFAULT 0 CHECK (conversions >= 0),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, ad_id, hour_start, group_code)
);

CREATE INDEX IF NOT EXISTS idx_ad_stat_hourly_tenant_hour
    ON leo_ads.ad_stat_hourly (tenant_id, hour_start DESC);

CREATE INDEX IF NOT EXISTS idx_ad_stat_hourly_placement_hour
    ON leo_ads.ad_stat_hourly (tenant_id, placement_id, hour_start DESC);

-- Anonymous grouped response patterns for low-history visitors. No
-- per-visitor membership is stored. Rows are only written when the cohort has
-- at least the configured minimum number of visitors (application rule), so
-- no individual can be identified from them.
CREATE TABLE IF NOT EXISTS leo_ads.cohort_pattern (
    tenant_id        BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    cohort_key       TEXT NOT NULL,
    ad_id            BIGINT NOT NULL,
    cohort_size      INTEGER NOT NULL CHECK (cohort_size >= 1),
    impressions      BIGINT NOT NULL DEFAULT 0 CHECK (impressions >= 0),
    objective_events BIGINT NOT NULL DEFAULT 0 CHECK (objective_events >= 0),
    refreshed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, cohort_key, ad_id)
);

-- ----------------------------------------------------------------------------
-- PART 4. SERVING DECISIONS AND ERASURE
-- ----------------------------------------------------------------------------

-- What was returned for one request, and why. One row per request that
-- reaches the ranking stage (personalised, hold-out and default outcomes), so
-- reports and audits are complete. Daily partitions, kept 90 days.
CREATE TABLE IF NOT EXISTS leo_ads.serving_decision (
    request_id       UUID NOT NULL,
    decided_at       TIMESTAMPTZ NOT NULL,
    tenant_id        BIGINT NOT NULL,
    -- Null when the visitor is not eligible for profiling.
    user_key_hash    BYTEA,
    placement_id     BIGINT,
    group_code       VARCHAR(30) NOT NULL
                     CHECK (group_code IN (
                         'personalised',
                         'holdout',
                         'default_no_history',
                         'default_no_consent',
                         'default_disabled',
                         'default_fallback'
                     )),
    -- [{"ad_id": int, "rank": int, "score": float, "signals": [...]}]
    chosen           JSONB NOT NULL DEFAULT '[]'::jsonb,
    candidate_count  INTEGER NOT NULL DEFAULT 0 CHECK (candidate_count >= 0),
    PRIMARY KEY (request_id, decided_at)
) PARTITION BY RANGE (decided_at);

CREATE TABLE IF NOT EXISTS leo_ads.serving_decision_default
    PARTITION OF leo_ads.serving_decision DEFAULT;

CREATE INDEX IF NOT EXISTS idx_serving_decision_tenant_time
    ON leo_ads.serving_decision (tenant_id, decided_at DESC);

CREATE INDEX IF NOT EXISTS idx_serving_decision_tenant_request
    ON leo_ads.serving_decision (tenant_id, request_id);

-- Daily partitions for today and the next seven days (same fallback as the
-- ad_event partitions above if rows already sit in the default partition).
DO $$
DECLARE
    day_start DATE;
    day_end   DATE;
    part_name TEXT;
    i         INTEGER;
BEGIN
    FOR i IN 0..7 LOOP
        day_start := (current_date + i);
        day_end   := (current_date + i + 1);
        part_name := 'serving_decision_' || to_char(day_start, 'YYYY_MM_DD');
        BEGIN
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS leo_ads.%I PARTITION OF leo_ads.serving_decision '
                'FOR VALUES FROM (%L) TO (%L)',
                part_name, day_start, day_end
            );
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Skipped partition leo_ads.%: % (partition_maintenance will fix up)',
                part_name, SQLERRM;
        END;
    END LOOP;
END
$$;

-- Audit trail for data-subject erasure. A request is never deleted: it is the
-- evidence that erasure happened (and when).
CREATE TABLE IF NOT EXISTS leo_ads.erasure_request (
    erasure_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id        BIGINT NOT NULL REFERENCES leo_ads.tenant(tenant_id),
    user_key_hash    BYTEA NOT NULL,
    -- Urgent requests complete within 72 hours; others within 30 days.
    urgent           BOOLEAN NOT NULL DEFAULT FALSE,
    status           VARCHAR(20) NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'redis_purged', 'completed', 'failed')),
    requested_by     TEXT,
    requested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at     TIMESTAMPTZ,
    -- Rows removed per table, set only at completion.
    deleted_counts   JSONB,
    failure_reason   TEXT,
    CHECK ((status = 'completed') = (completed_at IS NOT NULL)),
    CHECK (status = 'completed' OR deleted_counts IS NULL)
);

CREATE INDEX IF NOT EXISTS idx_erasure_request_open
    ON leo_ads.erasure_request (tenant_id, status, urgent DESC, requested_at)
    WHERE status <> 'completed';

COMMIT;
