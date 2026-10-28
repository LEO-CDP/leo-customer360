-- ============================================================================
-- Upgrade: per-journey-stage reports (View report -> KPIs, trend, breakdown)
-- Target : customer360 schema, PostgreSQL 16+. Idempotent; safe to re-run.
-- Needs  : 017_agent_campagn_journey.sql first.
--
-- Processing model
--   1. cdp_journey_metric        what each stage reports (catalog, seeded below)
--   2. refresh_journey_metrics_pg()  computes Postgres-sourced metrics nightly
--      event-lake metrics (visitors, leads, ...) are pushed by the lake job
--      with the same upsert into cdp_journey_metric_daily
--   3. cdp_journey_metric_daily  one snapshot row per metric / window / dimension
--   4. vw_journey_stage_report   current value + previous window + change
-- ============================================================================

BEGIN;

ALTER TABLE customer360.sys_tenant
    ADD COLUMN IF NOT EXISTS business_model TEXT NOT NULL DEFAULT 'B2C';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'customer360.sys_tenant'::regclass
          AND conname = 'sys_tenant_business_model_check'
    ) THEN
        ALTER TABLE customer360.sys_tenant
            ADD CONSTRAINT sys_tenant_business_model_check
            CHECK (business_model IN ('B2B', 'B2C', 'B2B2C'));
    END IF;
END $$;

COMMENT ON COLUMN customer360.sys_tenant.business_type IS
    'Industry or business domain (for example, retail); not the B2B/B2C/B2B2C go-to-market model.';
COMMENT ON COLUMN customer360.sys_tenant.business_model IS
    'Tenant go-to-market model: B2B, B2C, or B2B2C. Defaults to B2C.';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'customer360' AND table_name = 'crm_campaign'
          AND column_name = 'journey_stage'
    ) THEN
        RAISE EXCEPTION 'Apply migration 017_agent_campagn_journey.sql first: crm_campaign.journey_stage is missing';
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 1. Metric catalog (global; rules are data, not code)
--    journey_stage NULL = cross-stage metric computed for every stage (campaign_*).
--    rule for event_lake metrics lists the event names to count; leave
--    event_names empty until you map them to your event vocabulary.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customer360.cdp_journey_metric (
    metric_code VARCHAR(60) PRIMARY KEY,
    journey_stage VARCHAR(30) CHECK (journey_stage IS NULL OR journey_stage IN (
        'awareness', 'acquisition', 'activation', 'engagement',
        'conversion', 'retention', 'loyalty', 'churned')),
    label TEXT NOT NULL,
    unit VARCHAR(20) NOT NULL DEFAULT 'count'
        CHECK (unit IN ('count', 'percent', 'ratio', 'currency')),
    source VARCHAR(20) NOT NULL
        CHECK (source IN ('event_lake', 'profile', 'transaction', 'campaign')),
    applicable_business_models TEXT[] NOT NULL
        DEFAULT ARRAY['B2B', 'B2C', 'B2B2C']::TEXT[]
        CONSTRAINT cdp_journey_metric_business_models_check CHECK (
            cardinality(applicable_business_models) > 0
            AND applicable_business_models <@ ARRAY['B2B', 'B2C', 'B2B2C']::TEXT[]
        ),
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    higher_is_better BOOLEAN NOT NULL DEFAULT TRUE,
    rule JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(rule) = 'object'),
    display_order SMALLINT NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'customer360.cdp_journey_metric'::regclass
          AND conname = 'cdp_journey_metric_journey_stage_check'
          AND pg_get_constraintdef(oid) LIKE '%churned%'
    ) THEN
        ALTER TABLE customer360.cdp_journey_metric
            DROP CONSTRAINT IF EXISTS cdp_journey_metric_journey_stage_check;
        ALTER TABLE customer360.cdp_journey_metric
            ADD CONSTRAINT cdp_journey_metric_journey_stage_check
            CHECK (journey_stage IS NULL OR journey_stage IN (
                'awareness', 'acquisition', 'activation', 'engagement',
                'conversion', 'retention', 'loyalty', 'churned'));
    END IF;
END $$;

COMMENT ON TABLE customer360.cdp_journey_metric IS
    'KPI definitions shown on each journey stage report. source tells which job computes it; rule holds its parameters.';
COMMENT ON COLUMN customer360.cdp_journey_metric.applicable_business_models IS
    'Business models for which this KPI is valid (B2B, B2C, B2B2C).';

INSERT INTO customer360.cdp_journey_metric
    (metric_code, journey_stage, label, unit, source, is_primary, higher_is_better, rule, display_order)
VALUES
    ('visitors',               'awareness',   'Visitors',              'count',   'event_lake',  TRUE,  TRUE,  '{"event_names": [], "distinct": "profile"}', 10),
    ('awareness_conversion',   'awareness',   'Conversion to next',    'percent', 'event_lake',  FALSE, TRUE,  '{}', 20),
    ('new_leads',              'acquisition', 'New leads',             'count',   'event_lake',  TRUE,  TRUE,  '{"event_names": [], "distinct": "profile"}', 10),
    ('acquisition_conversion', 'acquisition', 'Conversion to next',    'percent', 'event_lake',  FALSE, TRUE,  '{}', 20),
    ('activated_users',        'activation',  'Activated users',       'count',   'event_lake',  TRUE,  TRUE,  '{"event_names": [], "distinct": "profile"}', 10),
    ('activation_conversion',  'activation',  'Conversion to next',    'percent', 'event_lake',  FALSE, TRUE,  '{}', 20),
    ('active_customers',       'engagement',  'Active customers',      'count',   'transaction', TRUE,  TRUE,  '{}', 10),
    ('purchase_frequency',     'engagement',  'Purchase frequency',    'ratio',   'transaction', FALSE, TRUE,  '{}', 20),
    ('orders',                 'conversion',  'Orders',                'count',   'transaction', TRUE,  TRUE,  '{"exclude_status": ["cancelled", "failed", "refunded"]}', 10),
    ('conversion_rate',        'conversion',  'Conversion rate',       'percent', 'event_lake',  FALSE, TRUE,  '{}', 20),
    ('retention_rate',         'retention',   'Retention rate',        'percent', 'transaction', TRUE,  TRUE,  '{}', 10),
    ('churn_risk_rate',        'retention',   'Churn risk',            'percent', 'profile',     FALSE, FALSE, '{"min_probability": 0.7}', 20),
    ('repeat_purchase_rate',   'loyalty',     'Repeat purchase',       'percent', 'transaction', TRUE,  TRUE,  '{}', 10),
    ('referral_rate',          'loyalty',     'Referral rate',         'percent', 'event_lake',  FALSE, TRUE,  '{"event_names": []}', 20),
    ('churn_rate',             'churned',     'Churn rate',            'percent', 'transaction', TRUE,  FALSE, '{}', 10),
    ('winback_rate',           'churned',     'Win-back rate',         'percent', 'event_lake',  FALSE, TRUE,  '{"event_names": []}', 20),
    ('campaign_spend',         NULL,          'Campaign spend',        'currency','campaign',    FALSE, FALSE, '{}', 90),
    ('campaign_conversions',   NULL,          'Campaign conversions',  'count',   'campaign',    FALSE, TRUE,  '{}', 91),
    ('campaign_revenue',       NULL,          'Campaign revenue',      'currency','campaign',    FALSE, TRUE,  '{}', 92)
ON CONFLICT (metric_code) DO NOTHING;

CREATE TABLE IF NOT EXISTS customer360.cdp_journey_metric_domain (
    metric_code VARCHAR(60) NOT NULL
        REFERENCES customer360.cdp_journey_metric(metric_code) ON DELETE CASCADE,
    domain_code VARCHAR(50)
        REFERENCES customer360.sys_domain(domain_code) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_cdp_journey_metric_domain
        UNIQUE NULLS NOT DISTINCT (metric_code, domain_code)
);

COMMENT ON TABLE customer360.cdp_journey_metric_domain IS
    'Explicit scope for each journey KPI: NULL domain_code means general; a sys_domain code means a domain-specific definition. Seed only supported scopes.';

INSERT INTO customer360.cdp_journey_metric_domain (metric_code, domain_code)
SELECT metric_code, NULL
  FROM customer360.cdp_journey_metric
ON CONFLICT (metric_code, domain_code) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 2. Daily snapshot. window_days = trailing window ending on report_date, so
--    unique counts and rates stay correct (daily values cannot be summed).
--    Rates keep numerator and denominator for drill-down and re-aggregation.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customer360.cdp_journey_metric_daily (
    snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id) ON DELETE CASCADE,
    report_date DATE NOT NULL,
    window_days SMALLINT NOT NULL CHECK (window_days IN (1, 7, 30, 90)),
    business_model TEXT NOT NULL DEFAULT 'B2C'
        CONSTRAINT cdp_journey_metric_daily_business_model_check
        CHECK (business_model IN ('B2B', 'B2C', 'B2B2C')),
    domain_code VARCHAR(50)
        REFERENCES customer360.sys_domain(domain_code) ON DELETE RESTRICT,
    journey_stage VARCHAR(30) NOT NULL CHECK (journey_stage IN (
        'awareness', 'acquisition', 'activation', 'engagement',
        'conversion', 'retention', 'loyalty', 'churned')),
    metric_code VARCHAR(60) NOT NULL
        REFERENCES customer360.cdp_journey_metric(metric_code) ON DELETE RESTRICT,
    dim_type VARCHAR(20) NOT NULL DEFAULT 'all'
        CHECK (dim_type IN ('all', 'channel', 'campaign', 'segment', 'agent')),
    dim_value TEXT NOT NULL DEFAULT '',
    numerator NUMERIC(18, 4),
    denominator NUMERIC(18, 4),
    value NUMERIC(18, 4) NOT NULL,
    currency TEXT NOT NULL DEFAULT ''
        CONSTRAINT cdp_journey_metric_daily_currency_check
        CHECK (currency = '' OR currency ~ '^[A-Z]{3}$'),
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_cdp_journey_metric_daily_scope
        UNIQUE NULLS NOT DISTINCT (
            tenant_id, report_date, window_days, business_model, domain_code,
            journey_stage, metric_code, dim_type, dim_value, currency
        )
);

COMMENT ON TABLE customer360.cdp_journey_metric_daily IS
    'Snapshot of journey-stage KPIs. Percent values are stored 0-100. currency separates campaign-derived metrics by ISO 4217 currency (UNK means unset); currencies are never combined.';
COMMENT ON COLUMN customer360.cdp_journey_metric_daily.currency IS
    'ISO 4217 currency for campaign-derived metrics; empty for non-campaign metrics and UNK when campaign currency is unset. No cross-currency totals are combined.';

CREATE INDEX IF NOT EXISTS idx_journey_metric_daily_latest
    ON customer360.cdp_journey_metric_daily (tenant_id, journey_stage, window_days, report_date DESC);

ALTER TABLE customer360.cdp_journey_metric_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.cdp_journey_metric_daily FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.cdp_journey_metric_daily;
CREATE POLICY tenant_policy ON customer360.cdp_journey_metric_daily
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);

-- ----------------------------------------------------------------------------
-- 3. Nightly job for Postgres-sourced metrics
--    Call once per tenant after midnight: SELECT customer360.refresh_journey_metrics_pg(:tenant);
--    Computes: orders, active_customers, purchase_frequency, retention_rate,
--    repeat_purchase_rate, churn_risk_rate, campaign_spend/conversions/revenue.
--    churn_risk_rate is a point-in-time value (profiles as they are now), so do
--    not use this function to backfill it for past dates.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION customer360.refresh_journey_metrics_pg(
    p_tenant UUID,
    p_date DATE DEFAULT (CURRENT_DATE - 1)
) RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    w INTEGER;
    n INTEGER := 0;
    k INTEGER;
    v_business_model TEXT;
    v_excl TEXT[];
    v_thr NUMERIC;
    v_from TIMESTAMP;
    v_prev_from TIMESTAMP;
    v_to TIMESTAMP := (p_date + 1)::timestamp;
    v_orders BIGINT;
    v_buyers BIGINT;
    v_buyer_orders BIGINT;
    v_repeat BIGINT;
    v_prev_buyers BIGINT;
    v_retained BIGINT;
    v_hi BIGINT;
    v_scored BIGINT;
BEGIN
    IF p_tenant IS NULL THEN
        RAISE EXCEPTION 'p_tenant must not be NULL';
    END IF;
    IF p_date IS NULL THEN
        RAISE EXCEPTION 'p_date must not be NULL';
    END IF;
    IF NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid
       IS DISTINCT FROM p_tenant THEN
        RAISE EXCEPTION 'app.tenant_id must match p_tenant';
    END IF;

    SELECT COALESCE(business_model, 'B2C')
      INTO v_business_model
      FROM customer360.sys_tenant
     WHERE tenant_id = p_tenant;
    v_business_model := COALESCE(v_business_model, 'B2C');

    SELECT COALESCE(ARRAY(SELECT lower(x) FROM jsonb_array_elements_text(m.rule -> 'exclude_status') AS x), ARRAY[]::TEXT[])
      INTO v_excl
      FROM customer360.cdp_journey_metric m WHERE m.metric_code = 'orders';
    v_excl := COALESCE(v_excl, ARRAY[]::TEXT[]);

    -- Replace only this function's metrics for the requested snapshot date.
    -- Event-lake metrics are written independently and remain untouched.
    DELETE FROM customer360.cdp_journey_metric_daily
     WHERE tenant_id = p_tenant
       AND report_date = p_date
       AND business_model = v_business_model
       AND (
           metric_code IN (
               'orders', 'active_customers', 'purchase_frequency',
               'retention_rate', 'repeat_purchase_rate', 'churn_rate',
               'campaign_spend', 'campaign_conversions', 'campaign_revenue'
           )
           OR (metric_code = 'churn_risk_rate' AND window_days = 1)
           OR (
               domain_code IS NOT NULL
               AND metric_code IN (
                   'orders', 'active_customers', 'purchase_frequency',
                   'retention_rate', 'repeat_purchase_rate', 'churn_rate', 'churn_risk_rate'
               )
           )
       );

    FOREACH w IN ARRAY ARRAY[1, 7, 30, 90] LOOP
        v_from := (p_date - (w - 1))::timestamp;
        v_prev_from := (p_date - (2 * w - 1))::timestamp;

        SELECT count(*) INTO v_orders
          FROM customer360.crm_transactions t
         WHERE t.tenant_id = p_tenant
           AND t.transaction_time >= v_from AND t.transaction_time < v_to
           AND COALESCE(lower(t.transaction_status), '') <> ALL (v_excl);

        WITH cur AS (
            SELECT t.master_profile_id, count(*) AS orders
              FROM customer360.crm_transactions t
             WHERE t.tenant_id = p_tenant AND t.master_profile_id IS NOT NULL
               AND t.transaction_time >= v_from AND t.transaction_time < v_to
               AND COALESCE(lower(t.transaction_status), '') <> ALL (v_excl)
             GROUP BY t.master_profile_id
        )
        SELECT count(*), COALESCE(sum(orders), 0), count(*) FILTER (WHERE orders >= 2)
          INTO v_buyers, v_buyer_orders, v_repeat
          FROM cur;

        WITH prev AS (
            SELECT DISTINCT t.master_profile_id
              FROM customer360.crm_transactions t
             WHERE t.tenant_id = p_tenant AND t.master_profile_id IS NOT NULL
               AND t.transaction_time >= v_prev_from AND t.transaction_time < v_from
               AND COALESCE(lower(t.transaction_status), '') <> ALL (v_excl)
        ), cur AS (
            SELECT DISTINCT t.master_profile_id
              FROM customer360.crm_transactions t
             WHERE t.tenant_id = p_tenant AND t.master_profile_id IS NOT NULL
               AND t.transaction_time >= v_from AND t.transaction_time < v_to
               AND COALESCE(lower(t.transaction_status), '') <> ALL (v_excl)
        )
        SELECT count(*), count(*) FILTER (WHERE prev.master_profile_id IN (SELECT master_profile_id FROM cur))
          INTO v_prev_buyers, v_retained
          FROM prev;

        INSERT INTO customer360.cdp_journey_metric_daily
            (tenant_id, report_date, window_days, business_model, domain_code,
             journey_stage, metric_code, dim_type, dim_value, numerator, denominator, value)
        SELECT p_tenant, p_date, w, v_business_model, NULL, s.stage, s.code, 'all', '', s.num, s.den, s.val
          FROM (VALUES
              ('conversion', 'orders',               v_orders::numeric,       NULL::numeric,         v_orders::numeric),
              ('engagement', 'active_customers',     v_buyers::numeric,       NULL::numeric,         v_buyers::numeric),
              ('engagement', 'purchase_frequency',   v_buyer_orders::numeric, v_buyers::numeric,     ROUND(v_buyer_orders::numeric / NULLIF(v_buyers, 0), 4)),
              ('retention',  'retention_rate',       v_retained::numeric,     v_prev_buyers::numeric, ROUND(100.0 * v_retained / NULLIF(v_prev_buyers, 0), 4)),
              ('loyalty',    'repeat_purchase_rate', v_repeat::numeric,       v_buyers::numeric,     ROUND(100.0 * v_repeat / NULLIF(v_buyers, 0), 4)),
              ('churned',    'churn_rate',           (v_prev_buyers - v_retained)::numeric, v_prev_buyers::numeric, ROUND(100.0 * (v_prev_buyers - v_retained) / NULLIF(v_prev_buyers, 0), 4))
          ) AS s(stage, code, num, den, val)
          JOIN customer360.cdp_journey_metric m
            ON m.metric_code = s.code
           AND m.is_active
           AND v_business_model = ANY(m.applicable_business_models)
          JOIN customer360.cdp_journey_metric_domain md
            ON md.metric_code = m.metric_code AND md.domain_code IS NULL
         WHERE s.val IS NOT NULL
           AND (s.code NOT IN ('retention_rate', 'churn_rate') OR w >= 7)
           AND (s.code <> 'repeat_purchase_rate' OR w >= 30)
        ON CONFLICT (tenant_id, report_date, window_days, business_model, domain_code,
                     journey_stage, metric_code, dim_type, dim_value, currency)
        DO UPDATE SET numerator = EXCLUDED.numerator, denominator = EXCLUDED.denominator,
                      value = EXCLUDED.value, computed_at = now();
        GET DIAGNOSTICS k = ROW_COUNT;
        n := n + k;

        WITH current_orders AS (
            SELECT p.domain AS domain_code, t.master_profile_id, count(*) AS order_count
              FROM customer360.crm_transactions t
              JOIN customer360.cdp_master_profiles p
                ON p.tenant_id = t.tenant_id
               AND p.master_profile_id = t.master_profile_id
              JOIN customer360.sys_domain d ON d.domain_code = p.domain AND d.is_active
             WHERE t.tenant_id = p_tenant
               AND p.domain <> '_'
               AND t.transaction_time >= v_from AND t.transaction_time < v_to
               AND COALESCE(lower(t.transaction_status), '') <> ALL (v_excl)
             GROUP BY p.domain, t.master_profile_id
        ),
        previous_buyers AS (
            SELECT DISTINCT p.domain AS domain_code, t.master_profile_id
              FROM customer360.crm_transactions t
              JOIN customer360.cdp_master_profiles p
                ON p.tenant_id = t.tenant_id
               AND p.master_profile_id = t.master_profile_id
              JOIN customer360.sys_domain d ON d.domain_code = p.domain AND d.is_active
             WHERE t.tenant_id = p_tenant
               AND p.domain <> '_'
               AND t.transaction_time >= v_prev_from AND t.transaction_time < v_from
               AND COALESCE(lower(t.transaction_status), '') <> ALL (v_excl)
        ),
        previous_counts AS (
            SELECT domain_code, count(*) AS previous_buyers
              FROM previous_buyers
             GROUP BY domain_code
        ),
        current_counts AS (
            SELECT domain_code,
                   sum(order_count) AS orders,
                   count(*) AS buyers,
                   count(*) FILTER (WHERE order_count >= 2) AS repeat_buyers
              FROM current_orders
             GROUP BY domain_code
        ),
        retained_counts AS (
            SELECT p.domain_code, count(*) AS retained_buyers
              FROM previous_buyers p
              JOIN current_orders c
                ON c.domain_code = p.domain_code
               AND c.master_profile_id = p.master_profile_id
             GROUP BY p.domain_code
        ),
        domain_stats AS (
            SELECT d.domain_code,
                   COALESCE(c.orders, 0) AS orders,
                   COALESCE(c.buyers, 0) AS buyers,
                   COALESCE(c.orders, 0) AS buyer_orders,
                   COALESCE(c.repeat_buyers, 0) AS repeat_buyers,
                   COALESCE(p.previous_buyers, 0) AS previous_buyers,
                   COALESCE(r.retained_buyers, 0) AS retained_buyers
              FROM (
                  SELECT domain_code FROM current_counts
                  UNION
                  SELECT domain_code FROM previous_counts
              ) d
              LEFT JOIN current_counts c USING (domain_code)
              LEFT JOIN previous_counts p USING (domain_code)
              LEFT JOIN retained_counts r USING (domain_code)
        )
        INSERT INTO customer360.cdp_journey_metric_daily
            (tenant_id, report_date, window_days, business_model, domain_code,
             journey_stage, metric_code, dim_type, dim_value,
             numerator, denominator, value)
        SELECT p_tenant, p_date, w, v_business_model, g.domain_code,
               s.stage, s.code, 'all', '', s.num, s.den, s.val
          FROM domain_stats g
          CROSS JOIN LATERAL (VALUES
              ('conversion', 'orders', g.orders::numeric, NULL::numeric, g.orders::numeric),
              ('engagement', 'active_customers', g.buyers::numeric, NULL::numeric, g.buyers::numeric),
              ('engagement', 'purchase_frequency', g.buyer_orders::numeric, g.buyers::numeric,
                  ROUND(g.buyer_orders::numeric / NULLIF(g.buyers, 0), 4)),
              ('retention', 'retention_rate', g.retained_buyers::numeric, g.previous_buyers::numeric,
                  ROUND(100.0 * g.retained_buyers / NULLIF(g.previous_buyers, 0), 4)),
              ('loyalty', 'repeat_purchase_rate', g.repeat_buyers::numeric, g.buyers::numeric,
                  ROUND(100.0 * g.repeat_buyers / NULLIF(g.buyers, 0), 4)),
              ('churned', 'churn_rate', (g.previous_buyers - g.retained_buyers)::numeric, g.previous_buyers::numeric,
                  ROUND(100.0 * (g.previous_buyers - g.retained_buyers) / NULLIF(g.previous_buyers, 0), 4))
          ) AS s(stage, code, num, den, val)
          JOIN customer360.cdp_journey_metric m
            ON m.metric_code = s.code
           AND m.is_active
           AND v_business_model = ANY(m.applicable_business_models)
          JOIN customer360.cdp_journey_metric_domain md
            ON md.metric_code = m.metric_code AND md.domain_code = g.domain_code
         WHERE s.val IS NOT NULL
           AND (s.code NOT IN ('retention_rate', 'churn_rate') OR w >= 7)
           AND (s.code <> 'repeat_purchase_rate' OR w >= 30)
        ON CONFLICT (tenant_id, report_date, window_days, business_model, domain_code,
                     journey_stage, metric_code, dim_type, dim_value, currency)
        DO UPDATE SET numerator = EXCLUDED.numerator, denominator = EXCLUDED.denominator,
                      value = EXCLUDED.value, computed_at = now();
        GET DIAGNOSTICS k = ROW_COUNT;
        n := n + k;

        -- Campaign metrics per stage, campaign and active assigned agent.
        INSERT INTO customer360.cdp_journey_metric_daily
            (tenant_id, report_date, window_days, business_model, domain_code,
             journey_stage, metric_code, dim_type, dim_value, value, currency)
        WITH campaign_totals AS (
            SELECT c.campaign_id,
                   c.journey_stage,
                   COALESCE(
                       upper(NULLIF(btrim(c.currency::text), '')),
                       'UNK'
                   ) AS currency,
                   COALESCE(sum(pd.spend), 0) AS spend,
                   COALESCE(sum(pd.conversions), 0) AS conv,
                   COALESCE(sum(pd.revenue_estimated), 0) AS rev
              FROM customer360.crm_campaign_performance_daily pd
              JOIN customer360.crm_campaign c
                ON c.tenant_id = pd.tenant_id AND c.campaign_id = pd.campaign_id
             WHERE pd.tenant_id = p_tenant
               AND c.journey_stage IS NOT NULL
               AND pd.report_date BETWEEN (p_date - (w - 1)) AND p_date
             GROUP BY c.campaign_id, c.journey_stage,
                      COALESCE(
                          upper(NULLIF(btrim(c.currency::text), '')),
                          'UNK'
                      )
        ),
        metric_dims AS (
            SELECT g.journey_stage,
                   g.currency,
                   'all'::VARCHAR(20) AS dim_type,
                   ''::TEXT AS dim_value,
                   sum(g.spend) AS spend,
                   sum(g.conv) AS conv,
                   sum(g.rev) AS rev
              FROM campaign_totals g
             GROUP BY g.journey_stage, g.currency
            UNION ALL
            SELECT g.journey_stage,
                   g.currency,
                   'campaign'::VARCHAR(20) AS dim_type,
                   g.campaign_id::text AS dim_value,
                   g.spend,
                   g.conv,
                   g.rev
              FROM campaign_totals g
            UNION ALL
            -- Full campaign metrics are attributed to each active agent; agent totals
            -- are not additive when a campaign has multiple assigned agents.
            SELECT g.journey_stage,
                   g.currency,
                   'agent'::VARCHAR(20) AS dim_type,
                   a.agent_code AS dim_value,
                   sum(g.spend) AS spend,
                   sum(g.conv) AS conv,
                   sum(g.rev) AS rev
              FROM campaign_totals g
              JOIN (
                  SELECT DISTINCT tenant_id, campaign_id, agent_code
                    FROM customer360.crm_campaign_agent
                   WHERE tenant_id = p_tenant AND is_active
              ) a ON a.tenant_id = p_tenant AND a.campaign_id = g.campaign_id
             GROUP BY g.journey_stage, g.currency, a.agent_code
        )
        SELECT p_tenant, p_date, w, v_business_model, NULL,
               g.journey_stage, v.code, g.dim_type,
               g.dim_value, v.val, g.currency
          FROM metric_dims g
         CROSS JOIN LATERAL (VALUES
              ('campaign_spend', g.spend),
              ('campaign_conversions', g.conv::numeric),
              ('campaign_revenue', g.rev)
          ) AS v(code, val)
         JOIN customer360.cdp_journey_metric m
           ON m.metric_code = v.code
          AND m.is_active
          AND v_business_model = ANY(m.applicable_business_models)
         JOIN customer360.cdp_journey_metric_domain md
           ON md.metric_code = m.metric_code AND md.domain_code IS NULL
        ON CONFLICT (tenant_id, report_date, window_days, business_model, domain_code,
                     journey_stage, metric_code, dim_type, dim_value, currency)
        DO UPDATE SET value = EXCLUDED.value, computed_at = now();
        GET DIAGNOSTICS k = ROW_COUNT;
        n := n + k;
    END LOOP;

    -- Churn risk: point-in-time share of scored active profiles above the threshold
    SELECT COALESCE((m.rule ->> 'min_probability')::numeric, 0.7) INTO v_thr
      FROM customer360.cdp_journey_metric m WHERE m.metric_code = 'churn_risk_rate';
    v_thr := COALESCE(v_thr, 0.7);

    SELECT count(*) FILTER (WHERE churn_probability >= v_thr), count(*)
      INTO v_hi, v_scored
      FROM customer360.cdp_master_profiles
     WHERE tenant_id = p_tenant AND status_code = 1 AND churn_probability IS NOT NULL;

    IF v_scored > 0 THEN
        IF EXISTS (
            SELECT 1
              FROM customer360.cdp_journey_metric m
              JOIN customer360.cdp_journey_metric_domain md
                ON md.metric_code = m.metric_code AND md.domain_code IS NULL
             WHERE m.metric_code = 'churn_risk_rate'
               AND m.is_active
               AND v_business_model = ANY(m.applicable_business_models)
        ) THEN
        INSERT INTO customer360.cdp_journey_metric_daily
            (tenant_id, report_date, window_days, business_model, domain_code,
             journey_stage, metric_code, dim_type, dim_value, numerator, denominator, value)
        VALUES (p_tenant, p_date, 1, v_business_model, NULL,
                'retention', 'churn_risk_rate', 'all', '',
                v_hi, v_scored, ROUND(100.0 * v_hi / v_scored, 4))
        ON CONFLICT (tenant_id, report_date, window_days, business_model, domain_code,
                     journey_stage, metric_code, dim_type, dim_value, currency)
        DO UPDATE SET numerator = EXCLUDED.numerator, denominator = EXCLUDED.denominator,
                      value = EXCLUDED.value, computed_at = now();
        GET DIAGNOSTICS k = ROW_COUNT;
        n := n + k;
        END IF;
    END IF;

    INSERT INTO customer360.cdp_journey_metric_daily
        (tenant_id, report_date, window_days, business_model, domain_code,
         journey_stage, metric_code, dim_type, dim_value, numerator, denominator, value)
    SELECT p_tenant, p_date, 1, v_business_model, p.domain,
           'retention', 'churn_risk_rate', 'all', '',
           count(*) FILTER (WHERE p.churn_probability >= v_thr),
           count(*),
           ROUND(100.0 * count(*) FILTER (WHERE p.churn_probability >= v_thr) / NULLIF(count(*), 0), 4)
      FROM customer360.cdp_master_profiles p
      JOIN customer360.sys_domain d ON d.domain_code = p.domain AND d.is_active
      JOIN customer360.cdp_journey_metric m
        ON m.metric_code = 'churn_risk_rate'
       AND m.is_active
       AND v_business_model = ANY(m.applicable_business_models)
      JOIN customer360.cdp_journey_metric_domain md
        ON md.metric_code = m.metric_code AND md.domain_code = p.domain
     WHERE p.tenant_id = p_tenant
       AND p.status_code = 1
       AND p.domain <> '_'
       AND p.churn_probability IS NOT NULL
     GROUP BY p.domain
    ON CONFLICT (tenant_id, report_date, window_days, business_model, domain_code,
                 journey_stage, metric_code, dim_type, dim_value, currency)
    DO UPDATE SET numerator = EXCLUDED.numerator, denominator = EXCLUDED.denominator,
                  value = EXCLUDED.value, computed_at = now();
    GET DIAGNOSTICS k = ROW_COUNT;
    n := n + k;

    RETURN n;
END;
$$;

COMMENT ON FUNCTION customer360.refresh_journey_metrics_pg(UUID, DATE) IS
    'Upserts Postgres-sourced journey metrics for windows 1/7/30/90 days ending p_date. Returns rows written. Run per tenant nightly.';

-- ----------------------------------------------------------------------------
-- 4. Report view: current value, previous window and change.
--    change_kind = points for percent metrics, percent for everything else.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW customer360.vw_journey_stage_report
WITH (security_invoker = true) AS
SELECT cur.tenant_id,
       cur.report_date,
       cur.window_days,
       cur.journey_stage,
       cur.metric_code,
       m.label,
       m.unit,
       m.is_primary,
       m.higher_is_better,
       m.display_order,
       cur.dim_type,
       cur.dim_value,
       cur.value,
       prev.value AS previous_value,
       CASE
           WHEN prev.value IS NULL THEN NULL
           WHEN m.unit = 'percent' THEN ROUND(cur.value - prev.value, 2)
           WHEN prev.value = 0 THEN NULL
           ELSE ROUND(100.0 * (cur.value - prev.value) / prev.value, 2)
       END AS change,
       CASE WHEN m.unit = 'percent' THEN 'points' ELSE 'percent' END AS change_kind,
       cur.currency,
       cur.business_model,
       cur.domain_code
  FROM customer360.cdp_journey_metric_daily cur
  JOIN customer360.cdp_journey_metric m
    ON m.metric_code = cur.metric_code
   AND m.is_active
   AND cur.business_model = ANY(m.applicable_business_models)
  JOIN customer360.cdp_journey_metric_domain md
    ON md.metric_code = m.metric_code
   AND md.domain_code IS NOT DISTINCT FROM cur.domain_code
  LEFT JOIN customer360.cdp_journey_metric_daily prev
    ON prev.tenant_id = cur.tenant_id
   AND prev.window_days = cur.window_days
   AND prev.journey_stage = cur.journey_stage
   AND prev.metric_code = cur.metric_code
   AND prev.dim_type = cur.dim_type
   AND prev.dim_value = cur.dim_value
   AND prev.business_model = cur.business_model
   AND prev.domain_code IS NOT DISTINCT FROM cur.domain_code
   AND prev.currency = cur.currency
   AND prev.report_date = cur.report_date - cur.window_days::int;

COMMIT;