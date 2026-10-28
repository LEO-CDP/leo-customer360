-- ============================================================================
-- LEO CDP: Cross-industry Customer Lifetime Value (CLV) valuation table
-- PostgreSQL 16+ | Schema: customer360
--
-- Prerequisite: run the existing Customer 360 schema first. This migration
-- reuses customer360.sys_domain, sys_tenant, and cdp_master_profiles.
-- ============================================================================

BEGIN;



CREATE TABLE IF NOT EXISTS customer360.cdp_customer_valuation (
    -- Identity and multi-tenant scope
    valuation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL
        REFERENCES customer360.sys_tenant(tenant_id) ON DELETE CASCADE,
    master_profile_id UUID NOT NULL
        REFERENCES customer360.cdp_master_profiles(master_profile_id) ON DELETE CASCADE,
    domain_code VARCHAR(50) NOT NULL
        REFERENCES customer360.sys_domain(domain_code) ON UPDATE CASCADE ON DELETE RESTRICT,

    -- A snapshot is reproducible by date + model/version. Reprocessing the same
    -- snapshot key updates it; new as_of_date/model_version keeps history.
    as_of_date DATE NOT NULL DEFAULT CURRENT_DATE,
    currency CHAR(3) NOT NULL,
    model_name VARCHAR(100) NOT NULL DEFAULT 'discounted_contribution_clv',
    model_version VARCHAR(50) NOT NULL DEFAULT '1.0.0',
    feature_version VARCHAR(50) NOT NULL DEFAULT '1.0.0',

    -- Customer lifecycle and transaction/engagement features shared by domains
    customer_tenure_days INTEGER CHECK (customer_tenure_days IS NULL OR customer_tenure_days >= 0),
    days_since_last_transaction INTEGER CHECK (days_since_last_transaction IS NULL OR days_since_last_transaction >= 0),
    days_since_last_engagement INTEGER CHECK (days_since_last_engagement IS NULL OR days_since_last_engagement >= 0),
    active_months_12m SMALLINT CHECK (active_months_12m IS NULL OR active_months_12m BETWEEN 0 AND 12),
    active_days_30d INTEGER CHECK (active_days_30d IS NULL OR active_days_30d >= 0),
    transactions_30d BIGINT CHECK (transactions_30d IS NULL OR transactions_30d >= 0),
    transactions_90d BIGINT CHECK (transactions_90d IS NULL OR transactions_90d >= 0),
    transactions_12m BIGINT CHECK (transactions_12m IS NULL OR transactions_12m >= 0),
    sessions_30d BIGINT CHECK (sessions_30d IS NULL OR sessions_30d >= 0),
    engagement_events_30d BIGINT CHECK (engagement_events_30d IS NULL OR engagement_events_30d >= 0),
    product_views_30d BIGINT CHECK (product_views_30d IS NULL OR product_views_30d >= 0),
    add_to_cart_30d BIGINT CHECK (add_to_cart_30d IS NULL OR add_to_cart_30d >= 0),

    -- Realized economics from the lookback period. These are historical facts,
    -- not forecasts. Negative contribution profit can occur and is permitted.
    historical_revenue_12m NUMERIC(20, 4) CHECK (historical_revenue_12m IS NULL OR historical_revenue_12m >= 0),
    recurring_revenue_12m NUMERIC(20, 4) CHECK (recurring_revenue_12m IS NULL OR recurring_revenue_12m >= 0),
    average_transaction_value_12m NUMERIC(20, 4) CHECK (average_transaction_value_12m IS NULL OR average_transaction_value_12m >= 0),
    historical_gross_profit NUMERIC(20, 4),
    historical_contribution_profit_12m NUMERIC(20, 4),
    acquisition_cost NUMERIC(20, 4) CHECK (acquisition_cost IS NULL OR acquisition_cost >= 0),

    -- Canonical forecast inputs. Domain adapters/feature pipelines should map
    -- domain-specific economics into these fields where possible.
    retention_probability_12m NUMERIC(7, 6)
        CHECK (retention_probability_12m IS NULL OR retention_probability_12m BETWEEN 0 AND 1),
    churn_probability_12m NUMERIC(7, 6)
        CHECK (churn_probability_12m IS NULL OR churn_probability_12m BETWEEN 0 AND 1),
    expected_transactions_per_year NUMERIC(18, 6)
        CHECK (expected_transactions_per_year IS NULL OR expected_transactions_per_year >= 0),
    expected_value_per_transaction NUMERIC(20, 4)
        CHECK (expected_value_per_transaction IS NULL OR expected_value_per_transaction >= 0),
    gross_margin_rate NUMERIC(7, 6)
        CHECK (gross_margin_rate IS NULL OR gross_margin_rate BETWEEN 0 AND 1),
    expected_annual_revenue NUMERIC(20, 4)
        CHECK (expected_annual_revenue IS NULL OR expected_annual_revenue >= 0),
    expected_annual_contribution_profit NUMERIC(20, 4),
    expected_annual_service_cost NUMERIC(20, 4)
        CHECK (expected_annual_service_cost IS NULL OR expected_annual_service_cost >= 0),
    expected_annual_marketing_cost NUMERIC(20, 4)
        CHECK (expected_annual_marketing_cost IS NULL OR expected_annual_marketing_cost >= 0),
    discount_rate NUMERIC(7, 6)
        CHECK (discount_rate IS NULL OR discount_rate >= 0),
    annual_value_growth_rate NUMERIC(7, 6) NOT NULL DEFAULT 0
        CHECK (annual_value_growth_rate > -1),
    forecast_horizon_years SMALLINT NOT NULL DEFAULT 5
        CHECK (forecast_horizon_years BETWEEN 1 AND 30),

    -- Domain-specific forecast inputs and derived, privacy-minimized geography.
    -- Use expected annual monetary values in domain_features, e.g.:
    -- retail:        expected_annual_net_revenue, expected_annual_cogs,
    --                expected_annual_fulfillment_cost, expected_annual_service_cost,
    --                expected_annual_marketing_cost
    -- banking:       expected_annual_net_interest_income, expected_annual_fee_income,
    --                expected_annual_credit_loss, expected_annual_operating_cost,
    --                expected_annual_marketing_cost
    -- insurance:     expected_annual_net_premium_revenue, expected_annual_fee_income,
    --                expected_annual_claims_cost, expected_annual_reinsurance_cost,
    --                expected_annual_commission_cost, expected_annual_service_cost,
    --                expected_annual_marketing_cost
    -- healthcare:    expected_annual_net_patient_revenue, expected_annual_other_revenue,
    --                expected_annual_clinical_cost, expected_annual_service_cost,
    --                expected_annual_marketing_cost
    -- telecom:       expected_annual_subscription_revenue, expected_annual_usage_revenue,
    --                expected_annual_other_revenue, expected_annual_network_cost,
    --                expected_annual_device_subsidy, expected_annual_service_cost,
    --                expected_annual_marketing_cost
    -- travel:        expected_annual_commission_revenue, expected_annual_ancillary_margin,
    --                expected_annual_fulfillment_cost, expected_annual_refund_cost,
    --                expected_annual_service_cost, expected_annual_marketing_cost
    -- real_estate:   expected_annual_commission_revenue, expected_annual_rental_revenue,
    --                expected_annual_transaction_cost, expected_annual_property_operating_cost,
    --                expected_annual_service_cost, expected_annual_marketing_cost
    -- education:     expected_annual_tuition_revenue, expected_annual_other_revenue,
    --                expected_annual_delivery_cost, expected_annual_student_service_cost,
    --                expected_annual_marketing_cost
    -- manufacturing: expected_annual_product_revenue, expected_annual_service_contract_revenue,
    --                expected_annual_cogs, expected_annual_warranty_cost,
    --                expected_annual_service_cost, expected_annual_marketing_cost
    -- media:         expected_annual_subscription_revenue, expected_annual_advertising_revenue,
    --                expected_annual_content_cost, expected_annual_delivery_cost,
    --                expected_annual_service_cost, expected_annual_marketing_cost
    -- hospitality:   expected_annual_food_beverage_revenue, expected_annual_room_revenue,
    --                expected_annual_other_revenue, expected_annual_food_beverage_cost,
    --                expected_annual_labor_operating_cost, expected_annual_marketing_cost
    -- For domain-specific calculation, provide every listed component; use 0 for
    -- a component that is confirmed not applicable. Do not silently treat missing
    -- cost data as zero. `expected_annual_contribution_profit` takes precedence.
    domain_features JSONB NOT NULL DEFAULT '{}'::jsonb,
    location_features JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_assumptions JSONB NOT NULL DEFAULT '{}'::jsonb,
    feature_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Valuation outputs. 12m/36m/horizon CLV figures are discounted contributions
    -- BEFORE acquisition cost. Horizon after-CAC is separately labeled.
    predicted_clv_12m NUMERIC(20, 4),
    predicted_clv_36m NUMERIC(20, 4),
    predicted_clv_horizon_before_cac NUMERIC(20, 4),
    predicted_clv_horizon_after_cac NUMERIC(20, 4),
    predicted_revenue_horizon NUMERIC(20, 4),
    predicted_contribution_profit_horizon NUMERIC(20, 4),

    -- `model_confidence` should be populated only by a calibrated predictive
    -- model. The deterministic calculator leaves it NULL.
    model_confidence NUMERIC(7, 6)
        CHECK (model_confidence IS NULL OR model_confidence BETWEEN 0 AND 1),
    data_quality_score NUMERIC(5, 2)
        CHECK (data_quality_score IS NULL OR data_quality_score BETWEEN 0 AND 100),
    clv_segment VARCHAR(50),

    -- Upstream profile / campaign scores. NULL means not calculated, not zero.
    purchase_intent_score NUMERIC(7, 4)
        CONSTRAINT ck_cdp_customer_valuation_purchase_intent_score CHECK (purchase_intent_score IS NULL OR purchase_intent_score BETWEEN 0 AND 100),
    churn_risk_score NUMERIC(7, 4)
        CONSTRAINT ck_cdp_customer_valuation_churn_risk_score CHECK (churn_risk_score IS NULL OR churn_risk_score BETWEEN 0 AND 100),
    next_best_product_score NUMERIC(7, 4)
        CONSTRAINT ck_cdp_customer_valuation_next_best_product_score CHECK (next_best_product_score IS NULL OR next_best_product_score BETWEEN 0 AND 100),
    incremental_campaign_value NUMERIC(20, 4),
    profile_quality_score NUMERIC(5, 2)
        CONSTRAINT ck_cdp_customer_valuation_profile_quality_score CHECK (profile_quality_score IS NULL OR profile_quality_score BETWEEN 0 AND 100),
    explanation JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- LLM-written narrative insight and structured, executable recommendation candidates.
    insights TEXT,
    actions JSONB NOT NULL DEFAULT '[]'::jsonb
        CONSTRAINT ck_cdp_customer_valuation_actions_array CHECK (jsonb_typeof(actions) = 'array'),

    calculation_status VARCHAR(30) NOT NULL DEFAULT 'PENDING'
        CHECK (calculation_status IN ('PENDING', 'COMPUTED', 'INSUFFICIENT_DATA', 'FAILED')),
    last_error TEXT,
    calculated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_cdp_customer_valuation_snapshot UNIQUE
        (tenant_id, master_profile_id, domain_code, as_of_date, model_name, model_version)
);

-- Idempotent upgrade path for databases where the base table was already created.
ALTER TABLE customer360.cdp_customer_valuation
    ADD COLUMN IF NOT EXISTS insights TEXT;
ALTER TABLE customer360.cdp_customer_valuation
    ADD COLUMN IF NOT EXISTS actions JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE customer360.cdp_customer_valuation
    ADD COLUMN IF NOT EXISTS historical_gross_profit NUMERIC(20, 4);
ALTER TABLE customer360.cdp_customer_valuation
    ADD COLUMN IF NOT EXISTS purchase_intent_score NUMERIC(7, 4);
ALTER TABLE customer360.cdp_customer_valuation
    ADD COLUMN IF NOT EXISTS churn_risk_score NUMERIC(7, 4);
ALTER TABLE customer360.cdp_customer_valuation
    ADD COLUMN IF NOT EXISTS next_best_product_score NUMERIC(7, 4);
ALTER TABLE customer360.cdp_customer_valuation
    ADD COLUMN IF NOT EXISTS incremental_campaign_value NUMERIC(20, 4);
ALTER TABLE customer360.cdp_customer_valuation
    ADD COLUMN IF NOT EXISTS profile_quality_score NUMERIC(5, 2);
UPDATE customer360.cdp_customer_valuation SET actions = '[]'::jsonb WHERE actions IS NULL;
ALTER TABLE customer360.cdp_customer_valuation
    ALTER COLUMN actions SET DEFAULT '[]'::jsonb;
ALTER TABLE customer360.cdp_customer_valuation
    ALTER COLUMN actions SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'customer360.cdp_customer_valuation'::regclass
          AND conname = 'ck_cdp_customer_valuation_actions_array'
    ) THEN
        ALTER TABLE customer360.cdp_customer_valuation
            ADD CONSTRAINT ck_cdp_customer_valuation_actions_array
            CHECK (jsonb_typeof(actions) = 'array');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'customer360.cdp_customer_valuation'::regclass AND conname = 'ck_cdp_customer_valuation_purchase_intent_score') THEN
        ALTER TABLE customer360.cdp_customer_valuation ADD CONSTRAINT ck_cdp_customer_valuation_purchase_intent_score CHECK (purchase_intent_score IS NULL OR purchase_intent_score BETWEEN 0 AND 100);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'customer360.cdp_customer_valuation'::regclass AND conname = 'ck_cdp_customer_valuation_churn_risk_score') THEN
        ALTER TABLE customer360.cdp_customer_valuation ADD CONSTRAINT ck_cdp_customer_valuation_churn_risk_score CHECK (churn_risk_score IS NULL OR churn_risk_score BETWEEN 0 AND 100);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'customer360.cdp_customer_valuation'::regclass AND conname = 'ck_cdp_customer_valuation_next_best_product_score') THEN
        ALTER TABLE customer360.cdp_customer_valuation ADD CONSTRAINT ck_cdp_customer_valuation_next_best_product_score CHECK (next_best_product_score IS NULL OR next_best_product_score BETWEEN 0 AND 100);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'customer360.cdp_customer_valuation'::regclass AND conname = 'ck_cdp_customer_valuation_profile_quality_score') THEN
        ALTER TABLE customer360.cdp_customer_valuation ADD CONSTRAINT ck_cdp_customer_valuation_profile_quality_score CHECK (profile_quality_score IS NULL OR profile_quality_score BETWEEN 0 AND 100);
    END IF;
END $$;

COMMENT ON TABLE customer360.cdp_customer_valuation IS
'Cross-industry customer lifetime value feature snapshot and model-output table. Inputs include shared lifecycle/economic features plus JSONB domain features; outputs include discounted CLV, data quality, deterministic explanation, LLM insights text, and structured LLM recommendation actions. One row per tenant/profile/domain/as-of-date/model/version.';

COMMENT ON COLUMN customer360.cdp_customer_valuation.domain_features IS
'Industry-specific aggregate features and expected annual economics. Use canonical expected annual contribution profit when a trusted domain adapter has already computed it. Avoid raw PII and unnecessary sensitive data.';

COMMENT ON COLUMN customer360.cdp_customer_valuation.location_features IS
'Privacy-minimized derived location features such as service_region, nearest_branch_distance_km, or catchment_id; avoid storing precise raw location unless justified and authorized.';

COMMENT ON COLUMN customer360.cdp_customer_valuation.data_quality_score IS
'Input completeness score (0-100), not statistical model confidence or probability of prediction accuracy.';

COMMENT ON COLUMN customer360.cdp_customer_valuation.insights IS
'LLM-generated natural-language interpretation of a privacy-minimized valuation snapshot. Historical facts, forecasts, and assumptions should be distinguished.';

COMMENT ON COLUMN customer360.cdp_customer_valuation.actions IS
'JSON array of LLM-recommended actions. Each item includes action_type, priority, title, rationale, recommended_action, expected_outcome, metric_to_monitor, and time_horizon_days. Review before activating campaigns.';

COMMENT ON COLUMN customer360.cdp_customer_valuation.purchase_intent_score IS
'Optional upstream purchase-intent score on a 0-100 scale; NULL means not computed. It is a score, not a calibrated probability unless the producer explicitly calibrates it.';
COMMENT ON COLUMN customer360.cdp_customer_valuation.churn_risk_score IS
'Optional upstream churn-risk score on a 0-100 scale; NULL means not computed. The LLM must not treat it as a calibrated probability unless the producer explicitly calibrates it.';
COMMENT ON COLUMN customer360.cdp_customer_valuation.next_best_product_score IS
'Optional upstream product relevance score on a 0-100 scale; NULL means not computed.';
COMMENT ON COLUMN customer360.cdp_customer_valuation.incremental_campaign_value IS
'Optional upstream estimated incremental campaign value in the row currency. It should be causal-test-backed or explicitly labeled as a forecast.';
COMMENT ON COLUMN customer360.cdp_customer_valuation.profile_quality_score IS
'Optional upstream composite profile quality score (0-100) covering completeness, freshness and trust. Separate from the CLV engine input-completeness data_quality_score.';

CREATE INDEX IF NOT EXISTS idx_cdp_customer_valuation_tenant_domain_profile
    ON customer360.cdp_customer_valuation (tenant_id, domain_code, master_profile_id);

CREATE INDEX IF NOT EXISTS idx_cdp_customer_valuation_clv_rank
    ON customer360.cdp_customer_valuation (tenant_id, domain_code, predicted_clv_horizon_after_cac DESC NULLS LAST)
    WHERE calculation_status = 'COMPUTED';

CREATE INDEX IF NOT EXISTS idx_cdp_customer_valuation_pending
    ON customer360.cdp_customer_valuation (tenant_id, created_at)
    WHERE calculation_status = 'PENDING';

CREATE INDEX IF NOT EXISTS idx_cdp_customer_valuation_domain_features
    ON customer360.cdp_customer_valuation USING GIN (domain_features);

-- Match the tenant isolation convention used by the existing schema. The app
-- must execute set_config('app.tenant_id', '<tenant UUID>', true) in each txn.
ALTER TABLE customer360.cdp_customer_valuation ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.cdp_customer_valuation FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.cdp_customer_valuation;
CREATE POLICY tenant_policy ON customer360.cdp_customer_valuation
    USING (
        tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid
    )
    WITH CHECK (
        tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid
    );

COMMIT;
