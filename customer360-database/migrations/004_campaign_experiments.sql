-- Campaign A/B testing persistence and variant-attributed performance.
CREATE TABLE IF NOT EXISTS customer360.crm_campaign_experiments (
    experiment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    campaign_id UUID NOT NULL REFERENCES customer360.crm_campaign(campaign_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'Draft',
    primary_metric VARCHAR(50) NOT NULL DEFAULT 'conversions',
    start_date DATE,
    end_date DATE,
    winning_variant_id UUID,
    metadata JSONB,
    created_by UUID REFERENCES customer360.sys_user(user_id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    CONSTRAINT chk_campaign_experiment_status CHECK (status IN ('Draft', 'Running', 'Paused', 'Completed', 'Cancelled')),
    CONSTRAINT chk_campaign_experiment_metric CHECK (primary_metric IN ('conversions', 'revenue', 'roas', 'cvr')),
    CONSTRAINT chk_campaign_experiment_dates CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);

CREATE TABLE IF NOT EXISTS customer360.crm_campaign_experiment_variants (
    variant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    experiment_id UUID NOT NULL REFERENCES customer360.crm_campaign_experiments(experiment_id) ON DELETE CASCADE,
    variant_key VARCHAR(50) NOT NULL,
    name TEXT NOT NULL,
    segment_id UUID NOT NULL REFERENCES customer360.cdp_segments(segment_id),
    template_id UUID REFERENCES customer360.crm_message_templates(template_id) ON DELETE SET NULL,
    allocation_percentage NUMERIC(5, 2) NOT NULL CHECK (allocation_percentage >= 0 AND allocation_percentage <= 100),
    is_control BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(20) NOT NULL DEFAULT 'Draft',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    CONSTRAINT uq_campaign_experiment_variant_key UNIQUE (experiment_id, variant_key),
    CONSTRAINT chk_campaign_experiment_variant_status CHECK (status IN ('Draft', 'Running', 'Paused', 'Completed', 'Cancelled'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_campaign_experiment_tenant_id
    ON customer360.crm_campaign_experiments (tenant_id, experiment_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_campaign_experiment_variant_tenant_id
    ON customer360.crm_campaign_experiment_variants (tenant_id, experiment_id, variant_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_campaign_experiment_variant_tenant_variant
    ON customer360.crm_campaign_experiment_variants (tenant_id, variant_id);

ALTER TABLE customer360.crm_campaign_experiments
    DROP CONSTRAINT IF EXISTS fk_campaign_experiment_winner;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_campaign_experiment_winner'
          AND conrelid = 'customer360.crm_campaign_experiments'::regclass
    ) THEN
        ALTER TABLE customer360.crm_campaign_experiments
            ADD CONSTRAINT fk_campaign_experiment_winner
            FOREIGN KEY (tenant_id, experiment_id, winning_variant_id)
            REFERENCES customer360.crm_campaign_experiment_variants(tenant_id, experiment_id, variant_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

ALTER TABLE customer360.crm_campaign_performance_daily
    ADD COLUMN IF NOT EXISTS experiment_variant_id UUID;

ALTER TABLE customer360.crm_campaign_performance_daily
    DROP CONSTRAINT IF EXISTS fk_campaign_performance_experiment_variant;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_campaign_performance_experiment_variant_tenant'
          AND conrelid = 'customer360.crm_campaign_performance_daily'::regclass
    ) THEN
        ALTER TABLE customer360.crm_campaign_performance_daily
            ADD CONSTRAINT fk_campaign_performance_experiment_variant_tenant
            FOREIGN KEY (tenant_id, experiment_variant_id)
            REFERENCES customer360.crm_campaign_experiment_variants(tenant_id, variant_id)
            ON DELETE SET NULL;
    END IF;
END $$;

ALTER TABLE customer360.crm_campaign_performance_daily
    DROP CONSTRAINT IF EXISTS uq_campaign_daily_performance;
CREATE UNIQUE INDEX IF NOT EXISTS ux_campaign_daily_performance_base
    ON customer360.crm_campaign_performance_daily (tenant_id, campaign_id, report_date)
    WHERE experiment_variant_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_campaign_daily_performance_variant
    ON customer360.crm_campaign_performance_daily (tenant_id, campaign_id, report_date, experiment_variant_id)
    WHERE experiment_variant_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_campaign_experiments_tenant_campaign
    ON customer360.crm_campaign_experiments (tenant_id, campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_experiment_variants_experiment
    ON customer360.crm_campaign_experiment_variants (tenant_id, experiment_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_campaign_experiment_variant_tenant_experiment'
          AND conrelid = 'customer360.crm_campaign_experiment_variants'::regclass
    ) THEN
        ALTER TABLE customer360.crm_campaign_experiment_variants
            ADD CONSTRAINT fk_campaign_experiment_variant_tenant_experiment
            FOREIGN KEY (tenant_id, experiment_id)
            REFERENCES customer360.crm_campaign_experiments(tenant_id, experiment_id)
            ON DELETE CASCADE;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_campaign_performance_variant
    ON customer360.crm_campaign_performance_daily (tenant_id, experiment_variant_id, report_date DESC);

DO $$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['crm_campaign_experiments', 'crm_campaign_experiment_variants'] LOOP
        EXECUTE format('ALTER TABLE customer360.%I ENABLE ROW LEVEL SECURITY;', table_name);
        EXECUTE format('ALTER TABLE customer360.%I FORCE ROW LEVEL SECURITY;', table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_policy ON customer360.%I;', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_policy ON customer360.%I
                USING (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid)
                WITH CHECK (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid);',
            table_name
        );
    END LOOP;
END $$;
