-- Consolidated historical upgrade through 018. Run with psql and ON_ERROR_STOP=1.
-- Fresh schemas record this upgrade in database-schema.sql and skip this script.
SELECT pg_advisory_lock(hashtextextended('customer360.legacy_upgrade_through_018', 0));
CREATE TABLE IF NOT EXISTS customer360.sys_schema_upgrade_history (
    upgrade_key TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
SELECT NOT EXISTS (SELECT 1 FROM customer360.sys_schema_upgrade_history WHERE upgrade_key = 'legacy_through_018') AS legacy_upgrade_needed
\gset
\if :legacy_upgrade_needed

-- Historical step: 001_harden_tenant_rls_policies.sql
-- Existing databases do not rerun database-schema.sql automatically. Recreate
-- tenant policies so an unset/blank app.tenant_id fails closed instead of
-- raising an invalid UUID cast error.
DO $$
DECLARE
    table_name TEXT;
    tenant_tables TEXT[] := ARRAY[
        'sys_organization',
        'sys_user',
        'sys_userinfo',
        'sys_role',
        'sys_audit_log',
        'crm_campaign',
        'crm_campaign_performance_daily',
        'crm_campaign_member',
        'crm_lead',
        'crm_lead_source',
        'crm_contact',
        'crm_account',
        'crm_opportunity',
        'crm_industry',
        'crm_customer_contacts',
        'crm_transactions',
        'cdp_master_profiles',
        'cdp_raw_profiles_stage',
        'cdp_profile_links',
        'cdp_identity_index',
        'cdp_profile_merge_history',
        'cdp_relations',
        'cdp_domain_profiles',
        'cdp_segments',
        'cdp_content_items',
        'cdp_customer_personas',
        'cdp_persona_archetypes',
        'sys_data_source'
    ];
BEGIN
    FOREACH table_name IN ARRAY tenant_tables LOOP
        IF to_regclass(format('customer360.%I', table_name)) IS NULL THEN
            CONTINUE;
        END IF;

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
END;
$$;

-- Historical step: 002_profile_indexes.sql
-- The profile activity endpoints filter by master_profile_id and date. Tenant
-- is also applied by PostgreSQL RLS, so keep it first for selective scans.
DROP INDEX IF EXISTS customer360.idx_contacts_master_profile_date;

CREATE INDEX IF NOT EXISTS idx_contacts_tenant_master_date ON customer360.crm_customer_contacts (
    tenant_id,
    master_profile_id,
    contact_date DESC
)
WHERE
    master_profile_id IS NOT NULL
    AND contact_date IS NOT NULL;

-- Supports engagement/channel aggregates and timeline transaction ordering
-- without relying on the less selective master_profile_id-only index.
CREATE INDEX IF NOT EXISTS idx_transactions_tenant_master_time ON customer360.crm_transactions (
    tenant_id,
    master_profile_id,
    transaction_time DESC
)
WHERE
    master_profile_id IS NOT NULL
    AND transaction_time IS NOT NULL;


-- Indexes for the master-profile list date filters and default ordering.
CREATE INDEX IF NOT EXISTS idx_cdp_mp_tenant_activity_sort ON customer360.cdp_master_profiles (
    tenant_id,
    last_activity_at DESC NULLS LAST,
    updated_at DESC NULLS LAST,
    created_at DESC,
    master_profile_id ASC
);

CREATE INDEX IF NOT EXISTS idx_cdp_mp_tenant_last_activity ON customer360.cdp_master_profiles (
    tenant_id,
    last_activity_at DESC,
    master_profile_id ASC
)
WHERE
    last_activity_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cdp_mp_tenant_updated_at ON customer360.cdp_master_profiles (
    tenant_id,
    updated_at DESC,
    master_profile_id ASC
)
WHERE
    updated_at IS NOT NULL;

-- Historical step: 003_profile_tracking_identifier_filters.sql
DO $$
DECLARE
    current_tenant_id UUID;
BEGIN
    FOR current_tenant_id IN SELECT tenant_id FROM customer360.sys_tenant LOOP
        PERFORM set_config('app.tenant_id', current_tenant_id::text, true);
        UPDATE customer360.cdp_raw_profiles_stage
        SET
            anonymous_id = COALESCE(
                NULLIF(btrim(anonymous_id), ''),
                NULLIF(btrim(event_payload ->> 'anonymous_id'), ''),
                NULLIF(btrim(event_payload #>> '{identity,anonymous_id}'), '')
            ),
            device_fingerprint = COALESCE(
                NULLIF(btrim(device_fingerprint), ''),
                NULLIF(btrim(event_payload ->> 'device_fingerprint'), ''),
                NULLIF(btrim(event_payload #>> '{identity,device_fingerprint}'), '')
            )
        WHERE tenant_id = current_tenant_id AND event_payload IS NOT NULL;
    END LOOP;
    PERFORM set_config('app.tenant_id', '', true);
END $$;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_tenant_anonymous_id
    ON customer360.cdp_raw_profiles_stage (tenant_id, anonymous_id)
    WHERE anonymous_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_tenant_device_fingerprint
    ON customer360.cdp_raw_profiles_stage (tenant_id, device_fingerprint)
    WHERE device_fingerprint IS NOT NULL;


-- Historical step: 004_campaign_experiments.sql
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

ALTER TABLE customer360.crm_campaign_performance_daily
    DROP CONSTRAINT IF EXISTS fk_campaign_performance_experiment_variant_tenant;

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
            ON DELETE SET NULL (experiment_variant_id);
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


-- Historical step: 005_campaign_content_items.sql
-- Restore the campaign-to-content relation for databases initialized before
-- the agentic campaign schema included crm_campaign_content_items.
CREATE TABLE IF NOT EXISTS customer360.crm_campaign_content_items (
    campaign_content_item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    campaign_id UUID NOT NULL REFERENCES customer360.crm_campaign(campaign_id) ON DELETE CASCADE,
    content_item_id UUID NOT NULL REFERENCES customer360.cdp_content_items(content_item_id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    role VARCHAR(50),
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT uq_crm_campaign_content UNIQUE (campaign_id, content_item_id)
);

CREATE INDEX IF NOT EXISTS idx_crm_campaign_content_tenant
    ON customer360.crm_campaign_content_items (tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_campaign_content_campaign
    ON customer360.crm_campaign_content_items (campaign_id);
CREATE INDEX IF NOT EXISTS idx_crm_campaign_content_item
    ON customer360.crm_campaign_content_items (content_item_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_campaign_tenant_id
    ON customer360.crm_campaign (tenant_id, campaign_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cdp_content_items_tenant_id
    ON customer360.cdp_content_items (tenant_id, content_item_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_crm_campaign_content_tenant_campaign'
          AND conrelid = 'customer360.crm_campaign_content_items'::regclass
    ) THEN
        ALTER TABLE customer360.crm_campaign_content_items
            ADD CONSTRAINT fk_crm_campaign_content_tenant_campaign
            FOREIGN KEY (tenant_id, campaign_id)
            REFERENCES customer360.crm_campaign (tenant_id, campaign_id)
            ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_crm_campaign_content_tenant_item'
          AND conrelid = 'customer360.crm_campaign_content_items'::regclass
    ) THEN
        ALTER TABLE customer360.crm_campaign_content_items
            ADD CONSTRAINT fk_crm_campaign_content_tenant_item
            FOREIGN KEY (tenant_id, content_item_id)
            REFERENCES customer360.cdp_content_items (tenant_id, content_item_id)
            ON DELETE CASCADE;
    END IF;
END $$;

ALTER TABLE customer360.crm_campaign_content_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.crm_campaign_content_items FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.crm_campaign_content_items;
CREATE POLICY tenant_policy ON customer360.crm_campaign_content_items
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);


-- Historical step: 006_cdp_agent_workflow.sql
-- Add ordered segment/agent configuration to existing databases.
BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS ux_cdp_segments_tenant_id
    ON customer360.cdp_segments (tenant_id, segment_id);

CREATE TABLE IF NOT EXISTS customer360.cdp_agent_workflow (
    agent_workflow_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    segment_id UUID NOT NULL,
    agent_code VARCHAR(100) NOT NULL REFERENCES customer360.cdp_ai_agents(agent_code) ON DELETE RESTRICT,
    execution_order INTEGER NOT NULL CHECK (execution_order > 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    schedule_definition VARCHAR(100),
    candidate_content_item_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    configuration JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(configuration) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fk_cdp_agent_workflow_tenant_segment
        FOREIGN KEY (tenant_id, segment_id)
        REFERENCES customer360.cdp_segments (tenant_id, segment_id) ON DELETE CASCADE,
    CONSTRAINT uq_cdp_agent_workflow_segment_agent UNIQUE (tenant_id, segment_id, agent_code),
    CONSTRAINT uq_cdp_agent_workflow_execution_order
        UNIQUE (tenant_id, segment_id, execution_order) DEFERRABLE INITIALLY IMMEDIATE,
    CONSTRAINT chk_cdp_agent_workflow_candidates_dimension
        CHECK (cardinality(candidate_content_item_ids) = 0 OR array_ndims(candidate_content_item_ids) = 1)
);

-- Existing installations may already have the workflow table from the first
-- version of this migration. Add the scheduler column before comments and
-- indexes reference it, without requiring existing workflow rows to be rebuilt.
ALTER TABLE customer360.cdp_agent_workflow
    ADD COLUMN IF NOT EXISTS schedule_definition VARCHAR(100);

COMMENT ON TABLE customer360.cdp_agent_workflow IS
    'One configured AI-agent step per tenant/segment/agent. Defines a many-to-many relationship with the global agent registry; active steps execute sequentially in ascending execution_order. Different segments may execute concurrently.';
COMMENT ON COLUMN customer360.cdp_agent_workflow.execution_order IS
    'Positive queue position, lowest first. The workflow runner must await each step before starting the next; defer the unique constraint within a transaction when reordering steps.';
COMMENT ON COLUMN customer360.cdp_agent_workflow.schedule_definition IS
    'Optional five-field cron override for this segment-agent step. NULL means inherit the selected cdp_ai_agents.schedule_definition.';
COMMENT ON COLUMN customer360.cdp_agent_workflow.candidate_content_item_ids IS
    'Per-step content/product candidates from cdp_content_items. Active ranking_recommendation steps require at least one candidate; triggers enforce same-tenant references and restrict deletion or key changes while referenced.';
COMMENT ON COLUMN customer360.cdp_agent_workflow.configuration IS
    'Per-segment agent input/configuration overrides, such as ranking parameters. This is configuration, not workflow execution state.';

CREATE INDEX IF NOT EXISTS idx_cdp_agent_workflow_agent
    ON customer360.cdp_agent_workflow (agent_code, tenant_id, segment_id);
CREATE INDEX IF NOT EXISTS idx_cdp_agent_workflow_active_queue
    ON customer360.cdp_agent_workflow (tenant_id, segment_id, execution_order)
    WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_cdp_agent_workflow_candidates
    ON customer360.cdp_agent_workflow USING GIN (candidate_content_item_ids);

ALTER TABLE customer360.cdp_content_items
    ADD COLUMN IF NOT EXISTS embedding VECTOR;
ALTER TABLE customer360.cdp_content_items
    ADD COLUMN IF NOT EXISTS embedding_model TEXT;

CREATE OR REPLACE FUNCTION customer360.invalidate_content_item_embedding()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.domain IS DISTINCT FROM OLD.domain
       OR NEW.item_type IS DISTINCT FROM OLD.item_type
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.summary IS DISTINCT FROM OLD.summary
       OR NEW.segment_tags IS DISTINCT FROM OLD.segment_tags THEN
        NEW.embedding := NULL;
        NEW.embedding_model := NULL;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_invalidate_content_item_embedding
    ON customer360.cdp_content_items;
CREATE TRIGGER trg_invalidate_content_item_embedding
    BEFORE UPDATE OF domain, item_type, title, summary, segment_tags
    ON customer360.cdp_content_items
    FOR EACH ROW EXECUTE FUNCTION customer360.invalidate_content_item_embedding();

-- An array keeps candidate lists in the requested single relation table.
-- Lock referenced rows to serialize validation against deletion/key changes.
CREATE OR REPLACE FUNCTION customer360.validate_agent_workflow_candidates()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    candidate_id UUID;
BEGIN
    IF current_setting('transaction_isolation') = 'repeatable read' THEN
        RAISE EXCEPTION 'Workflow candidate writes require READ COMMITTED or SERIALIZABLE isolation'
            USING ERRCODE = '0A000';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM unnest(NEW.candidate_content_item_ids) AS candidate(item_id)
        GROUP BY item_id
        HAVING item_id IS NULL OR count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Workflow candidates must be distinct, non-null content item IDs'
            USING ERRCODE = '23514';
    END IF;

    FOR candidate_id IN
        SELECT item_id
        FROM unnest(NEW.candidate_content_item_ids) AS candidate(item_id)
        ORDER BY item_id
    LOOP
        PERFORM 1
        FROM customer360.cdp_content_items
        WHERE tenant_id = NEW.tenant_id AND content_item_id = candidate_id
        FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Workflow candidate % does not exist in tenant %', candidate_id, NEW.tenant_id
                USING ERRCODE = '23503';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validate_agent_workflow_candidates ON customer360.cdp_agent_workflow;
CREATE TRIGGER trg_validate_agent_workflow_candidates
    BEFORE INSERT OR UPDATE OF tenant_id, candidate_content_item_ids
    ON customer360.cdp_agent_workflow
    FOR EACH ROW EXECUTE FUNCTION customer360.validate_agent_workflow_candidates();

CREATE OR REPLACE FUNCTION customer360.restrict_workflow_candidate_changes()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant_id = OLD.tenant_id AND NEW.content_item_id = OLD.content_item_id THEN
            RETURN NEW;
        END IF;
    END IF;

    IF current_setting('transaction_isolation') = 'repeatable read' THEN
        RAISE EXCEPTION 'Content candidate deletion/key changes require READ COMMITTED or SERIALIZABLE isolation'
            USING ERRCODE = '0A000';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM customer360.cdp_agent_workflow
        WHERE tenant_id = OLD.tenant_id
          AND candidate_content_item_ids @> ARRAY[OLD.content_item_id]
    ) THEN
        RAISE EXCEPTION 'Content item % is referenced by an agent workflow', OLD.content_item_id
            USING ERRCODE = '23503';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_restrict_workflow_candidate_changes ON customer360.cdp_content_items;
CREATE TRIGGER trg_restrict_workflow_candidate_changes
    BEFORE DELETE OR UPDATE OF tenant_id, content_item_id
    ON customer360.cdp_content_items
    FOR EACH ROW EXECUTE FUNCTION customer360.restrict_workflow_candidate_changes();

ALTER TABLE customer360.cdp_agent_workflow ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.cdp_agent_workflow FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.cdp_agent_workflow;
CREATE POLICY tenant_policy ON customer360.cdp_agent_workflow
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);

CREATE TABLE IF NOT EXISTS customer360.cdp_profile_recommendation_runs (
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id) ON DELETE CASCADE,
    segment_id UUID NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    profile_count INTEGER NOT NULL CHECK (profile_count >= 0),
    step_count INTEGER NOT NULL CHECK (step_count > 0),
    profile_runs_processed INTEGER NOT NULL DEFAULT 0 CHECK (profile_runs_processed >= 0),
    recommendations_written INTEGER NOT NULL DEFAULT 0 CHECK (recommendations_written >= 0),
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT pk_cdp_profile_recommendation_runs PRIMARY KEY (tenant_id, segment_id, run_id),
    CONSTRAINT fk_cdp_profile_recommendation_runs_tenant_segment
        FOREIGN KEY (tenant_id, segment_id)
        REFERENCES customer360.cdp_segments (tenant_id, segment_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS customer360.cdp_profile_recommendations (
    tenant_id UUID NOT NULL,
    segment_id UUID NOT NULL,
    master_profile_id UUID NOT NULL,
    agent_code TEXT NOT NULL,
    run_id TEXT NOT NULL,
    content_item_id UUID NOT NULL,
    rank INTEGER NOT NULL CHECK (rank > 0),
    score DOUBLE PRECISION NOT NULL CHECK (score BETWEEN 0 AND 1),
    semantic_score DOUBLE PRECISION CHECK (semantic_score IS NULL OR semantic_score BETWEEN 0 AND 1),
    tag_score DOUBLE PRECISION NOT NULL CHECK (tag_score BETWEEN 0 AND 1),
    strategy TEXT NOT NULL CHECK (strategy IN ('tags', 'semantic', 'hybrid')),
    matched_tags TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
    reason TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_cdp_profile_recommendations PRIMARY KEY (
        tenant_id, segment_id, master_profile_id, agent_code, run_id, content_item_id
    ),
    CONSTRAINT fk_cdp_profile_recommendations_tenant_run
        FOREIGN KEY (tenant_id, segment_id, run_id)
        REFERENCES customer360.cdp_profile_recommendation_runs (tenant_id, segment_id, run_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_cdp_profile_recommendations_tenant_profile
        FOREIGN KEY (tenant_id, master_profile_id)
        REFERENCES customer360.cdp_master_profiles (tenant_id, master_profile_id) ON DELETE CASCADE,
    CONSTRAINT fk_cdp_profile_recommendations_tenant_content
        FOREIGN KEY (tenant_id, content_item_id)
        REFERENCES customer360.cdp_content_items (tenant_id, content_item_id) ON DELETE CASCADE
);

ALTER TABLE customer360.cdp_profile_recommendations
    ADD COLUMN IF NOT EXISTS semantic_score DOUBLE PRECISION;
ALTER TABLE customer360.cdp_profile_recommendations
    ADD COLUMN IF NOT EXISTS tag_score DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE customer360.cdp_profile_recommendations
    ADD COLUMN IF NOT EXISTS strategy TEXT NOT NULL DEFAULT 'tags';

CREATE INDEX IF NOT EXISTS idx_cdp_profile_recommendations_lookup
    ON customer360.cdp_profile_recommendations
        (tenant_id, master_profile_id, segment_id, run_id, rank);
CREATE INDEX IF NOT EXISTS idx_cdp_profile_recommendation_runs_latest
    ON customer360.cdp_profile_recommendation_runs
        (tenant_id, segment_id, status, completed_at DESC);
ALTER TABLE customer360.cdp_profile_recommendation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.cdp_profile_recommendation_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.cdp_profile_recommendation_runs;
CREATE POLICY tenant_policy ON customer360.cdp_profile_recommendation_runs
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);
ALTER TABLE customer360.cdp_profile_recommendations ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.cdp_profile_recommendations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.cdp_profile_recommendations;
CREATE POLICY tenant_policy ON customer360.cdp_profile_recommendations
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);

COMMIT;


-- Historical step: 007_cdp_product_items.sql
-- Preserve source product attributes and link each source identity to its
-- generated, customer-facing cdp_content_items representation.
BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS ux_cdp_content_items_tenant_id
    ON customer360.cdp_content_items (tenant_id, content_item_id);

CREATE TABLE IF NOT EXISTS customer360.cdp_product_items (
    product_item_id TEXT PRIMARY KEY DEFAULT (gen_random_uuid()::text),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id) ON DELETE CASCADE,
    content_item_id UUID,
    domain TEXT NOT NULL,
    product_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT '',
    product_id_type TEXT NOT NULL CHECK (btrim(product_id_type) <> ''),
    product_id TEXT NOT NULL CHECK (btrim(product_id) <> ''),
    keywords TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
    ext_attributes JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(ext_attributes) = 'object'),
    original_price NUMERIC(18, 4) CHECK (original_price IS NULL OR original_price >= 0),
    sale_price NUMERIC(18, 4) CHECK (sale_price IS NULL OR sale_price >= 0),
    currency VARCHAR(3),
    source_fields JSONB NOT NULL CHECK (jsonb_typeof(source_fields) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_cdp_product_items_tenant_content
        UNIQUE (tenant_id, content_item_id),
    CONSTRAINT fk_cdp_product_items_tenant_content
        FOREIGN KEY (tenant_id, content_item_id)
        REFERENCES customer360.cdp_content_items (tenant_id, content_item_id)
        ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED
);

ALTER TABLE customer360.cdp_product_items
    DROP CONSTRAINT IF EXISTS cdp_product_items_store_id_check;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'customer360'
          AND table_name = 'cdp_product_items'
          AND column_name = 'store_id'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'customer360'
          AND table_name = 'cdp_product_items'
          AND column_name = 'source_id'
    ) THEN
        ALTER TABLE customer360.cdp_product_items RENAME COLUMN store_id TO source_id;
    END IF;
END;
$$;

ALTER TABLE customer360.cdp_product_items
    ADD COLUMN IF NOT EXISTS source_id TEXT NOT NULL DEFAULT '';
ALTER TABLE customer360.cdp_product_items
    ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT '';
ALTER TABLE customer360.cdp_product_items
    ADD COLUMN IF NOT EXISTS ext_attributes JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(ext_attributes) = 'object');
ALTER TABLE customer360.cdp_product_items
    ALTER COLUMN content_item_id DROP NOT NULL;
ALTER TABLE customer360.cdp_product_items
    DROP CONSTRAINT IF EXISTS uq_cdp_product_items_source_identity;
ALTER TABLE customer360.cdp_product_items
    ADD CONSTRAINT uq_cdp_product_items_source_identity
        UNIQUE (tenant_id, source_type, source_id, product_id_type, product_id);

COMMENT ON TABLE customer360.cdp_product_items IS
    'Tenant-scoped source products imported from TSV; content_item_id is populated when LLM-generated cdp_content_items content is available. Re-imports upsert by tenant, source_type, source_id, and product identity.';
COMMENT ON COLUMN customer360.cdp_product_items.source_fields IS
    'Complete source TSV row as JSONB; all source fields are provided to the product-content LLM and retained for audit/re-generation.';
COMMENT ON COLUMN customer360.cdp_product_items.ext_attributes IS
    'Optional structured product metadata from the TSV ext_attributes JSON object.';

CREATE INDEX IF NOT EXISTS idx_cdp_product_items_tenant_domain
    ON customer360.cdp_product_items (tenant_id, domain);
CREATE INDEX IF NOT EXISTS idx_cdp_product_items_content
    ON customer360.cdp_product_items (tenant_id, content_item_id);

ALTER TABLE customer360.cdp_product_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.cdp_product_items FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.cdp_product_items;
CREATE POLICY tenant_policy ON customer360.cdp_product_items
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);

COMMIT;


-- Historical step: 008_content_embedding_contract.sql
-- Allow 384- and 768-dimensional recommendation vectors with matching indexes.
BEGIN;

ALTER TABLE customer360.cdp_content_items
    ADD COLUMN IF NOT EXISTS embedding_text TEXT,
    ADD COLUMN IF NOT EXISTS embedding_version VARCHAR(50),
    ADD COLUMN IF NOT EXISTS embedding_updated_at TIMESTAMPTZ;

DO $$
DECLARE
    current_tenant_id UUID;
BEGIN
    FOR current_tenant_id IN SELECT tenant_id FROM customer360.sys_tenant LOOP
        PERFORM set_config('app.tenant_id', current_tenant_id::text, true);
        UPDATE customer360.cdp_content_items
        SET segment_tags = ARRAY[]::TEXT[]
        WHERE tenant_id = current_tenant_id AND segment_tags IS NULL;
    END LOOP;
    PERFORM set_config('app.tenant_id', '', true);
END $$;
ALTER TABLE customer360.cdp_content_items
    ALTER COLUMN segment_tags SET DEFAULT ARRAY[]::TEXT[],
    ALTER COLUMN segment_tags SET NOT NULL;

DROP INDEX IF EXISTS customer360.idx_cdp_content_items_embedding_hnsw;
DROP INDEX IF EXISTS customer360.idx_cdp_product_items_embedding_hnsw;
ALTER TABLE customer360.cdp_content_items
    ALTER COLUMN embedding TYPE VECTOR
    USING embedding::VECTOR;

DO $$
DECLARE
    current_tenant_id UUID;
BEGIN
    FOR current_tenant_id IN SELECT tenant_id FROM customer360.sys_tenant LOOP
        PERFORM set_config('app.tenant_id', current_tenant_id::text, true);
        UPDATE customer360.cdp_content_items
        SET embedding = NULL,
            embedding_model = NULL,
            embedding_text = NULL,
            embedding_version = NULL,
            embedding_updated_at = NULL
        WHERE tenant_id = current_tenant_id AND embedding IS NOT NULL
          AND vector_dims(embedding) NOT IN (384, 768);
    END LOOP;
    PERFORM set_config('app.tenant_id', '', true);
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'customer360.cdp_content_items'::regclass
          AND conname = 'chk_cdp_content_items_embedding_dimensions'
    ) THEN
        ALTER TABLE customer360.cdp_content_items
            ADD CONSTRAINT chk_cdp_content_items_embedding_dimensions
            CHECK (embedding IS NULL OR vector_dims(embedding) IN (384, 768));
    END IF;
END;
$$;

ALTER TABLE customer360.cdp_product_items
    ADD COLUMN IF NOT EXISTS embedding_text TEXT,
    ADD COLUMN IF NOT EXISTS embedding VECTOR,
    ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(255),
    ADD COLUMN IF NOT EXISTS embedding_version VARCHAR(50),
    ADD COLUMN IF NOT EXISTS embedding_updated_at TIMESTAMPTZ;
ALTER TABLE customer360.cdp_product_items
    ALTER COLUMN embedding TYPE VECTOR
    USING embedding::VECTOR;

DO $$
DECLARE
    current_tenant_id UUID;
BEGIN
    FOR current_tenant_id IN SELECT tenant_id FROM customer360.sys_tenant LOOP
        PERFORM set_config('app.tenant_id', current_tenant_id::text, true);
        UPDATE customer360.cdp_product_items
        SET embedding = NULL,
            embedding_model = NULL,
            embedding_text = NULL,
            embedding_version = NULL,
            embedding_updated_at = NULL
        WHERE tenant_id = current_tenant_id AND embedding IS NOT NULL
          AND vector_dims(embedding) NOT IN (384, 768);
    END LOOP;
    PERFORM set_config('app.tenant_id', '', true);
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'customer360.cdp_product_items'::regclass
          AND conname = 'chk_cdp_product_items_embedding_dimensions'
    ) THEN
        ALTER TABLE customer360.cdp_product_items
            ADD CONSTRAINT chk_cdp_product_items_embedding_dimensions
            CHECK (embedding IS NULL OR vector_dims(embedding) IN (384, 768));
    END IF;
END;
$$;

DROP INDEX IF EXISTS customer360.idx_cdp_content_items_embedding_384_hnsw;
DROP INDEX IF EXISTS customer360.idx_cdp_content_items_embedding_768_hnsw;
CREATE INDEX idx_cdp_content_items_embedding_384_hnsw
    ON customer360.cdp_content_items
    USING hnsw ((embedding::VECTOR(384)) vector_cosine_ops)
    WHERE vector_dims(embedding) = 384;
CREATE INDEX idx_cdp_content_items_embedding_768_hnsw
    ON customer360.cdp_content_items
    USING hnsw ((embedding::VECTOR(768)) vector_cosine_ops)
    WHERE vector_dims(embedding) = 768;

DROP INDEX IF EXISTS customer360.idx_cdp_product_items_embedding_384_hnsw;
DROP INDEX IF EXISTS customer360.idx_cdp_product_items_embedding_768_hnsw;
CREATE INDEX idx_cdp_product_items_embedding_384_hnsw
    ON customer360.cdp_product_items
    USING hnsw ((embedding::VECTOR(384)) vector_cosine_ops)
    WHERE vector_dims(embedding) = 384;
CREATE INDEX idx_cdp_product_items_embedding_768_hnsw
    ON customer360.cdp_product_items
    USING hnsw ((embedding::VECTOR(768)) vector_cosine_ops)
    WHERE vector_dims(embedding) = 768;

DROP INDEX IF EXISTS customer360.idx_cdp_content_items_domain_type;
CREATE INDEX idx_cdp_content_items_domain_type
    ON customer360.cdp_content_items (tenant_id, domain, item_type);

CREATE OR REPLACE FUNCTION customer360.invalidate_content_item_embedding()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.domain IS DISTINCT FROM OLD.domain
       OR NEW.item_type IS DISTINCT FROM OLD.item_type
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.summary IS DISTINCT FROM OLD.summary
       OR NEW.segment_tags IS DISTINCT FROM OLD.segment_tags THEN
        NEW.embedding := NULL;
        NEW.embedding_text := NULL;
        NEW.embedding_model := NULL;
        NEW.embedding_version := NULL;
        NEW.embedding_updated_at := NULL;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION customer360.invalidate_product_item_embedding()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.domain IS DISTINCT FROM OLD.domain
       OR NEW.product_type IS DISTINCT FROM OLD.product_type
       OR NEW.keywords IS DISTINCT FROM OLD.keywords
       OR NEW.ext_attributes IS DISTINCT FROM OLD.ext_attributes
       OR NEW.source_fields IS DISTINCT FROM OLD.source_fields THEN
        NEW.embedding := NULL;
        NEW.embedding_text := NULL;
        NEW.embedding_model := NULL;
        NEW.embedding_version := NULL;
        NEW.embedding_updated_at := NULL;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_invalidate_product_item_embedding
    ON customer360.cdp_product_items;
CREATE TRIGGER trg_invalidate_product_item_embedding
    BEFORE UPDATE OF
        domain,
        product_type,
        keywords,
        ext_attributes,
        source_fields
    ON customer360.cdp_product_items
    FOR EACH ROW EXECUTE FUNCTION customer360.invalidate_product_item_embedding();

COMMENT ON COLUMN customer360.cdp_content_items.embedding IS
    '384- or 768-dimensional content embedding used for semantic and hybrid recommendation ranking.';
COMMENT ON COLUMN customer360.cdp_content_items.embedding_text IS
    'Canonical text embedded for recommendation ranking; refreshed when source content changes.';
COMMENT ON COLUMN customer360.cdp_content_items.embedding_model IS
    'Provider and model key used to generate the current content embedding.';
COMMENT ON COLUMN customer360.cdp_content_items.embedding_version IS
    'Application embedding contract version used to generate the current vector.';
COMMENT ON COLUMN customer360.cdp_content_items.embedding_updated_at IS
    'Time the current content embedding was generated.';
COMMENT ON COLUMN customer360.cdp_product_items.embedding_text IS
    'Canonical product text reserved for future product-vector generation.';
COMMENT ON COLUMN customer360.cdp_product_items.embedding IS
    '384- or 768-dimensional product embedding reserved for product similarity and recommendation.';
COMMENT ON COLUMN customer360.cdp_product_items.embedding_model IS
    'Provider and model key used to generate the current product embedding.';
COMMENT ON COLUMN customer360.cdp_product_items.embedding_version IS
    'Application embedding contract version used to generate the current product vector.';
COMMENT ON COLUMN customer360.cdp_product_items.embedding_updated_at IS
    'Time the current product embedding was generated.';

COMMIT;


-- Historical step: 009_assistant_messages.sql
-- LEO Assistant chat memory: one row per message (long-term tier; the last few rows of a
-- conversation are also the short-term window the model sees). See sys_assistant_message below.
BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS ux_sys_user_tenant_id
    ON customer360.sys_user (tenant_id, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cdp_master_profiles_tenant_id
    ON customer360.cdp_master_profiles (tenant_id, master_profile_id);

CREATE TABLE IF NOT EXISTS customer360.sys_assistant_message (
    message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    conversation_id UUID NOT NULL,
    user_id UUID NOT NULL,
    page VARCHAR(120),
    master_profile_id UUID,
    role VARCHAR(10) NOT NULL CHECK (role IN ('user', 'assistant')),
    message_text TEXT NOT NULL CHECK (char_length(message_text) <= 2000),
    status VARCHAR(20),
    clarify VARCHAR(10) CHECK (clarify IN ('accents', 'question')),
    sources JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(sources) = 'array'),
    -- clock_timestamp() (not now()) so a question and its answer written in one transaction keep their order.
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT fk_sys_assistant_message_tenant_user
        FOREIGN KEY (tenant_id, user_id)
        REFERENCES customer360.sys_user (tenant_id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_sys_assistant_message_tenant_profile
        FOREIGN KEY (tenant_id, master_profile_id)
        REFERENCES customer360.cdp_master_profiles (tenant_id, master_profile_id) ON DELETE CASCADE
);

-- Running summary of the whole chat so far, written by the model with each answer (assistant rows only).
-- ADD COLUMN IF NOT EXISTS so a database that already ran this file earlier gets it too.
ALTER TABLE customer360.sys_assistant_message
    ADD COLUMN IF NOT EXISTS summary TEXT CHECK (char_length(summary) <= 800);

COMMENT ON TABLE customer360.sys_assistant_message IS
    'LEO Assistant chat messages (long-term memory). One row per user question or assistant answer, grouped by conversation_id and bound to the page and customer the chat started on. message_text is stored with emails/phone numbers masked. Readable only by its author (every query filters user_id) for 30 days: reads ignore older rows and each write deletes them.';
COMMENT ON COLUMN customer360.sys_assistant_message.conversation_id IS
    'Client-held chat id (no conversation table). A request whose page or master_profile_id differs from the first message of the id starts a new conversation.';
COMMENT ON COLUMN customer360.sys_assistant_message.summary IS
    'For assistant rows: model-written summary of the whole conversation up to and including this answer (masked, at most 800 chars). The newest one is sent back to the model with the last exchange instead of the older messages.';
COMMENT ON COLUMN customer360.sys_assistant_message.sources IS
    'For assistant rows: the documents the answer used, [{path, title, heading}]. Empty for user rows and refusals.';

CREATE INDEX IF NOT EXISTS idx_sys_assistant_message_conversation
    ON customer360.sys_assistant_message (tenant_id, user_id, conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sys_assistant_message_scope
    ON customer360.sys_assistant_message (tenant_id, user_id, page, master_profile_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sys_assistant_message_created
    ON customer360.sys_assistant_message (tenant_id, created_at);

ALTER TABLE customer360.sys_assistant_message ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.sys_assistant_message FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.sys_assistant_message;
CREATE POLICY tenant_policy ON customer360.sys_assistant_message
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);

COMMIT;


-- Historical step: 010_campaign_workflow_link.sql
-- Link each campaign to the exact immutable workflow version it saves with.
ALTER TABLE customer360.crm_campaign
    ADD COLUMN IF NOT EXISTS workflow_id UUID,
    ADD COLUMN IF NOT EXISTS workflow_version INT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'crm_campaign_workflow_version_fk'
          AND conrelid = 'customer360.crm_campaign'::regclass
    ) THEN
        ALTER TABLE customer360.crm_campaign
            ADD CONSTRAINT crm_campaign_workflow_version_fk
            FOREIGN KEY (workflow_id, workflow_version, tenant_id)
            REFERENCES customer360.crm_flow_version (flow_id, version, tenant_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'crm_campaign_workflow_version_pair_check'
          AND conrelid = 'customer360.crm_campaign'::regclass
    ) THEN
        ALTER TABLE customer360.crm_campaign
            ADD CONSTRAINT crm_campaign_workflow_version_pair_check
            CHECK ((workflow_id IS NULL) = (workflow_version IS NULL));
    END IF;
END $$;


-- Historical step: 011_cdp_audience_tags.sql
-- IAB Audience Taxonomy 1.1 catalog (customer360.cdp_audience_tags) and
-- audience_tags TEXT[] columns on cdp_master_profiles and cdp_segments.
BEGIN;

CREATE TABLE IF NOT EXISTS customer360.cdp_audience_tags (
    tag_name TEXT PRIMARY KEY CHECK (btrim(tag_name) <> ''),
    unique_id TEXT NOT NULL CHECK (btrim(unique_id) <> ''),
    parent_id TEXT,
    taxonomy_version TEXT NOT NULL DEFAULT '1.1' CHECK (btrim(taxonomy_version) <> ''),
    condensed_name TEXT NOT NULL CHECK (btrim(condensed_name) <> ''),
    tier_1 TEXT NOT NULL CHECK (btrim(tier_1) <> ''),
    tier_2 TEXT,
    tier_3 TEXT,
    tier_4 TEXT,
    tier_5 TEXT,
    tier_6 TEXT,
    extension_notes TEXT,
    status_code SMALLINT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_cdp_audience_tags_version_unique_id
        UNIQUE (taxonomy_version, unique_id)
);

COMMENT ON TABLE customer360.cdp_audience_tags IS
    'IAB Audience Taxonomy 1.1 catalog keyed by tag_name (slugified tier path). Used to map first-party cdp_master_profiles and cdp_segments to standardized contextual and audience segmentation taxonomies for analytics, personalization, and programmatic advertising.';
COMMENT ON COLUMN customer360.cdp_audience_tags.tag_name IS
    'Primary key: deterministic slugified tier path (e.g. demographic/age-range/18-20) referenced by cdp_segments.audience_tags and cdp_master_profiles.audience_tags.';
COMMENT ON COLUMN customer360.cdp_audience_tags.unique_id IS
    'IAB Audience Taxonomy Unique ID from the official TSV specification.';
COMMENT ON COLUMN customer360.cdp_audience_tags.parent_id IS
    'Parent IAB Unique ID in the taxonomy hierarchy (NULL for Tier 1 root nodes).';
COMMENT ON COLUMN customer360.cdp_audience_tags.condensed_name IS
    'IAB Condensed Name (1st, 2nd, Last Tier) from the taxonomy TSV.';

CREATE INDEX IF NOT EXISTS idx_cdp_audience_tags_unique_id
    ON customer360.cdp_audience_tags (unique_id);
CREATE INDEX IF NOT EXISTS idx_cdp_audience_tags_parent_id
    ON customer360.cdp_audience_tags (parent_id)
    WHERE parent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cdp_audience_tags_tiers
    ON customer360.cdp_audience_tags (tier_1, tier_2, tier_3);
CREATE INDEX IF NOT EXISTS idx_cdp_audience_tags_status
    ON customer360.cdp_audience_tags (status_code);

ALTER TABLE customer360.cdp_master_profiles
    ADD COLUMN IF NOT EXISTS audience_tags TEXT[];

CREATE INDEX IF NOT EXISTS idx_cdp_mp_audience_tags
    ON customer360.cdp_master_profiles USING GIN (audience_tags);

ALTER TABLE customer360.cdp_segments
    ADD COLUMN IF NOT EXISTS audience_tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];

CREATE INDEX IF NOT EXISTS idx_cdp_segments_audience_tags
    ON customer360.cdp_segments USING GIN (audience_tags);

INSERT INTO customer360.cdp_profile_attributes (
    attribute_internal_code,
    master_profile_column,
    name,
    description,
    attribute_group,
    source_table,
    data_type,
    domain_scope,
    is_pii,
    status,
    is_identity_resolution,
    matching_rule,
    matching_threshold,
    consolidation_rule,
    is_ai_agent,
    agent_code,
    agent_version,
    value_type,
    value_min,
    value_max,
    refresh_frequency,
    display_order
) VALUES (
    'audience_tags',
    'audience_tags',
    'IAB Audience Tags',
    'Standardized IAB Audience Taxonomy tag names mapped from segments for audience analytics, personalization, and programmatic advertising.',
    'MARKETING',
    'cdp_master_profiles',
    'ARRAY',
    'all',
    FALSE,
    'ACTIVE',
    FALSE,
    NULL,
    NULL,
    NULL,
    FALSE,
    NULL,
    NULL,
    'label',
    NULL,
    NULL,
    NULL,
    365
)
ON CONFLICT (attribute_internal_code) DO UPDATE SET
    master_profile_column = EXCLUDED.master_profile_column,
    name                  = EXCLUDED.name,
    description           = EXCLUDED.description,
    attribute_group       = EXCLUDED.attribute_group,
    source_table          = EXCLUDED.source_table,
    data_type             = EXCLUDED.data_type,
    domain_scope          = EXCLUDED.domain_scope,
    is_pii                = EXCLUDED.is_pii,
    status                = EXCLUDED.status,
    value_type            = EXCLUDED.value_type,
    display_order         = EXCLUDED.display_order,
    updated_at            = now();

-- Backfill standardized IAB audience_tags on default cdp_segments (when empty)
-- and sync cdp_master_profiles.audience_tags across tenants.
DO $$
DECLARE
    current_tenant_id UUID;
BEGIN
    FOR current_tenant_id IN SELECT tenant_id FROM customer360.sys_tenant LOOP
        PERFORM set_config('app.tenant_id', current_tenant_id::text, true);
UPDATE customer360.cdp_segments
SET audience_tags = CASE
    WHEN segment_tag IN ('high_value', 'champions') THEN
        ARRAY['demographic/personal-finance/personal-level-affluence-band/high-net-worth']::text[]
    WHEN segment_tag IN ('growth_potential', 'hot_leads') THEN
        ARRAY['demographic/personal-finance/personal-level-affluence-band/mid-net-worth']::text[]
    WHEN segment_tag LIKE 'retail_%' OR segment_tag LIKE 'ecommerce_%' THEN
        ARRAY['interest/shopping', 'purchase-intent/consumer-packaged-goods']::text[]
    WHEN segment_tag LIKE 'travel_%' THEN
        ARRAY['interest/travel', 'purchase-intent/travel-and-tourism']::text[]
    WHEN segment_tag LIKE 'education_%' THEN
        ARRAY['interest/education', 'purchase-intent/education-and-careers']::text[]
    WHEN segment_tag LIKE 'real_estate_%' THEN
        ARRAY['interest/real-estate', 'purchase-intent/real-estate']::text[]
    ELSE audience_tags
END,
updated_at = now()
WHERE tenant_id = current_tenant_id
  AND COALESCE(cardinality(audience_tags), 0) = 0
  AND (
      segment_tag IN ('high_value', 'champions', 'growth_potential', 'hot_leads')
      OR segment_tag LIKE 'retail_%'
      OR segment_tag LIKE 'ecommerce_%'
      OR segment_tag LIKE 'travel_%'
      OR segment_tag LIKE 'education_%'
      OR segment_tag LIKE 'real_estate_%'
  );

UPDATE customer360.cdp_master_profiles AS mp
SET audience_tags = ARRAY(
    SELECT DISTINCT tag
    FROM (
        -- 1. Tags inherited from active matched segments in cdp_segments
        SELECT u.tag
        FROM customer360.cdp_segments AS s
        CROSS JOIN LATERAL unnest(COALESCE(s.audience_tags, ARRAY[]::text[])) AS u(tag)
        WHERE s.tenant_id = mp.tenant_id
          AND s.is_active = TRUE
          AND s.status_code = 1
          AND s.segment_tag = ANY(COALESCE(mp.segmentation_tags, ARRAY[]::text[]))
        UNION
        -- 2. Existing profile audience_tags
        SELECT e.tag
        FROM unnest(COALESCE(mp.audience_tags, ARRAY[]::text[])) AS e(tag)
        UNION
        -- 3. Standardized IAB Demographic Gender tag
        SELECT CASE lower(btrim(COALESCE(mp.gender, '')))
            WHEN 'female' THEN 'demographic/gender/female'
            WHEN 'male' THEN 'demographic/gender/male'
            WHEN 'other' THEN 'demographic/gender/other-gender'
            ELSE NULL
        END AS tag
        UNION
        -- 4. Standardized IAB Demographic Age Range tag
        SELECT CASE
            WHEN mp.date_of_birth IS NULL THEN NULL
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 18 AND 20 THEN 'demographic/age-range/18-20'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 21 AND 24 THEN 'demographic/age-range/21-24'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 25 AND 29 THEN 'demographic/age-range/25-29'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 30 AND 34 THEN 'demographic/age-range/30-34'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 35 AND 39 THEN 'demographic/age-range/35-39'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 40 AND 44 THEN 'demographic/age-range/40-44'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 45 AND 49 THEN 'demographic/age-range/45-49'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 50 AND 54 THEN 'demographic/age-range/50-54'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 55 AND 59 THEN 'demographic/age-range/55-59'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 60 AND 64 THEN 'demographic/age-range/60-64'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 65 AND 69 THEN 'demographic/age-range/65-69'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 70 AND 74 THEN 'demographic/age-range/70-74'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) >= 75 THEN 'demographic/age-range/75-plus'
            ELSE NULL
        END AS tag
        UNION
        -- 5. Standardized IAB Domain Interest tag
        SELECT CASE lower(btrim(COALESCE(mp.domain, '')))
            WHEN 'retail' THEN 'interest/shopping'
            WHEN 'banking' THEN 'interest/personal-finance'
            WHEN 'travel' THEN 'interest/travel'
            WHEN 'education' THEN 'interest/education'
            WHEN 'real_estate' THEN 'interest/real-estate'
            WHEN 'media' THEN 'interest/television'
            ELSE NULL
        END AS tag
    ) AS derived(tag)
    WHERE tag IS NOT NULL AND btrim(tag) <> ''
    ORDER BY tag
)
WHERE mp.tenant_id = current_tenant_id
  AND COALESCE(cardinality(mp.audience_tags), 0) = 0;
    END LOOP;
    PERFORM set_config('app.tenant_id', '', true);
END $$;

COMMIT;


-- Historical step: 012_cdp_customer_valuation.sql
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
DO $$
DECLARE
    current_tenant_id UUID;
BEGIN
    FOR current_tenant_id IN SELECT tenant_id FROM customer360.sys_tenant LOOP
        PERFORM set_config('app.tenant_id', current_tenant_id::text, true);
        UPDATE customer360.cdp_customer_valuation SET actions = '[]'::jsonb
        WHERE tenant_id = current_tenant_id AND actions IS NULL;
    END LOOP;
    PERFORM set_config('app.tenant_id', '', true);
END $$;
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


-- Historical step: 013_remove_graph_ml_agent_type.sql
BEGIN;

DO $$
DECLARE
    current_tenant_id UUID;
BEGIN
    FOR current_tenant_id IN SELECT tenant_id FROM customer360.sys_tenant LOOP
        PERFORM set_config('app.tenant_id', current_tenant_id::text, true);
        IF EXISTS (
            SELECT 1 FROM customer360.cdp_agent_workflow
            WHERE tenant_id = current_tenant_id
              AND agent_code = 'identity_graph_intelligence'
        ) THEN
            RAISE EXCEPTION
                'Remove identity_graph_intelligence workflow steps before applying migration 013';
        END IF;
    END LOOP;
    PERFORM set_config('app.tenant_id', '', true);

    IF EXISTS (
        SELECT 1
        FROM customer360.cdp_profile_attributes
        WHERE agent_code = 'identity_graph_intelligence'
    ) THEN
        RAISE EXCEPTION
            'Reassign identity_graph_intelligence profile attributes before applying migration 013';
    END IF;
END;
$$;

DELETE FROM customer360.cdp_ai_agents
WHERE agent_code = 'identity_graph_intelligence';

ALTER TABLE customer360.cdp_ai_agents
    DROP CONSTRAINT IF EXISTS cdp_ai_agents_model_type_check;
ALTER TABLE customer360.cdp_ai_agents
    DROP CONSTRAINT IF EXISTS chk_cdp_ai_agents_model_type;
ALTER TABLE customer360.cdp_ai_agents
    ADD CONSTRAINT chk_cdp_ai_agents_model_type
    CHECK (model_type IN (
        'classification',
        'regression',
        'clustering',
        'ranking_recommendation',
        'forecasting',
        'anomaly_detection',
        'uplift_modeling',
        'semantic_embedding',
        'optimization',
        'rules_engine',
        'generative_llm'
    ));

COMMIT;


-- Historical step: 014_activate_eligibility_policy.sql
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM customer360.cdp_ai_agents
        WHERE agent_code = 'eligibility_policy'
          AND model_type = 'rules_engine'
    ) THEN
        RAISE EXCEPTION
            'Seed eligibility_policy before applying migration 014';
    END IF;
END;
$$;

UPDATE customer360.cdp_ai_agents
SET status = 'ACTIVE',
    updated_at = now()
WHERE agent_code = 'eligibility_policy'
  AND model_type = 'rules_engine'
  AND status = 'INACTIVE';

COMMIT;


-- Historical step: 015_remove_semantic_embedding_agent_type.sql
BEGIN;

DO $$
DECLARE
    current_tenant_id UUID;
BEGIN
    FOR current_tenant_id IN SELECT tenant_id FROM customer360.sys_tenant LOOP
        PERFORM set_config('app.tenant_id', current_tenant_id::text, true);
        IF EXISTS (
            SELECT 1 FROM customer360.cdp_agent_workflow
            WHERE tenant_id = current_tenant_id
              AND agent_code = 'customer_semantic_profile'
        ) THEN
            RAISE EXCEPTION
                'Remove customer_semantic_profile workflow steps before applying migration 015';
        END IF;
    END LOOP;
    PERFORM set_config('app.tenant_id', '', true);

    IF EXISTS (
        SELECT 1
        FROM customer360.cdp_profile_attributes
        WHERE agent_code = 'customer_semantic_profile'
    ) THEN
        RAISE EXCEPTION
            'Reassign customer_semantic_profile attributes before applying migration 015';
    END IF;
END;
$$;

DELETE FROM customer360.cdp_ai_agents
WHERE agent_code = 'customer_semantic_profile';

ALTER TABLE customer360.cdp_ai_agents
    DROP CONSTRAINT IF EXISTS cdp_ai_agents_model_type_check;
ALTER TABLE customer360.cdp_ai_agents
    DROP CONSTRAINT IF EXISTS chk_cdp_ai_agents_model_type;
ALTER TABLE customer360.cdp_ai_agents
    ADD CONSTRAINT chk_cdp_ai_agents_model_type
    CHECK (model_type IN (
        'classification',
        'regression',
        'clustering',
        'ranking_recommendation',
        'forecasting',
        'anomaly_detection',
        'uplift_modeling',
        'optimization',
        'rules_engine',
        'generative_llm'
    ));

COMMIT;


-- Historical step: 016_add_unclassified_profile_domain.sql
-- Add the neutral profile-domain fallback used when tracking data has no
-- reliable business-domain classification.

INSERT INTO customer360.sys_domain (
    domain_code,
    domain_name,
    description,
    display_order,
    is_system,
    is_active
)
VALUES (
    '_',
    'Unclassified / Not set',
    'Profile has no reliable business-domain classification.',
    0,
    TRUE,
    TRUE
)
ON CONFLICT (domain_code) DO UPDATE
SET domain_name = EXCLUDED.domain_name,
    description = EXCLUDED.description,
    display_order = EXCLUDED.display_order,
    is_system = TRUE,
    is_active = TRUE,
    updated_at = NOW();

DO $$
DECLARE
    current_tenant_id UUID;
BEGIN
    FOR current_tenant_id IN
        SELECT tenant_id FROM customer360.sys_tenant ORDER BY tenant_id
    LOOP
        -- sys_tenant_domain is FORCE RLS-protected. Scope each tenant
        -- operation explicitly instead of relying on a superuser bypass.
        PERFORM set_config('app.tenant_id', current_tenant_id::text, true);

        INSERT INTO customer360.sys_tenant_domain (
            tenant_id,
            domain_id,
            is_default,
            is_active
        )
        SELECT
            current_tenant_id,
            domain_id,
            FALSE,
            TRUE
        FROM customer360.sys_domain
        WHERE domain_code = '_'
        ON CONFLICT (tenant_id, domain_id) DO UPDATE
        SET is_active = TRUE;

        UPDATE customer360.cdp_master_profiles
        SET domain = '_',
            updated_at = NOW()
        WHERE tenant_id = current_tenant_id
          AND (domain IS NULL OR BTRIM(domain) = '' OR domain = 'unknown');

        UPDATE customer360.cdp_raw_profiles_stage
        SET domain = '_'
        WHERE tenant_id = current_tenant_id
          AND (domain IS NULL OR BTRIM(domain) = '' OR domain = 'unknown');
    END LOOP;

    -- Do not leak a tenant context to statements after this migration.
    PERFORM set_config('app.tenant_id', '', true);
END;
$$;

ALTER TABLE customer360.cdp_master_profiles
    ALTER COLUMN domain SET DEFAULT '_';

ALTER TABLE customer360.cdp_raw_profiles_stage
    ALTER COLUMN domain SET DEFAULT '_';


-- Historical step: 017_agent_campagn_journey.sql
-- ============================================================================
-- Upgrade: connect Campaigns to AI Agents (smallest change set)
-- Target : customer360 schema, PostgreSQL 16+ (tested on 16, uses no 17/18-only syntax)
-- Safe to re-run (idempotent). Append to database-schema.sql or run as a migration.
--
-- What it adds
--   1. journey_stage on cdp_ai_agents and crm_campaign (8 stages shared by both)
--   2. AI node types on crm_flow_node + a generated agent_code column with an FK
--   3. crm_campaign_agent: which agents work on a campaign, in which role,
--      at which autonomy level, pinned to which prompt revision
--
-- What it deliberately does NOT add (already covered by your schema)
--   - workflow versions / pinning / review  -> crm_flow, crm_flow_version, crm_campaign.workflow_*
--   - agent proposals                       -> crm_flow_version (created_by = 'agent', InReview)
--   - prompt versions                       -> cdp_ai_agents.prompt_versions / instruction_version
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Journey stage on agents and campaigns
-- ----------------------------------------------------------------------------
ALTER TABLE customer360.cdp_ai_agents
    ADD COLUMN IF NOT EXISTS journey_stage VARCHAR(30);

ALTER TABLE customer360.crm_campaign
    ADD COLUMN IF NOT EXISTS journey_stage VARCHAR(30);

DO $$
DECLARE
    c RECORD;
    current_tenant_id UUID;
BEGIN
    FOR c IN
        SELECT * FROM (VALUES
            ('cdp_ai_agents', 'chk_cdp_ai_agents_journey_stage'),
            ('crm_campaign',  'chk_crm_campaign_journey_stage')
        ) AS t(table_name, constraint_name)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = c.constraint_name
              AND conrelid = format('customer360.%I', c.table_name)::regclass
              AND pg_get_constraintdef(oid) LIKE '%churned%'
        ) THEN
            EXECUTE format(
                'ALTER TABLE customer360.%I DROP CONSTRAINT IF EXISTS %I',
                c.table_name, c.constraint_name);
            EXECUTE format(
                'ALTER TABLE customer360.%I ADD CONSTRAINT %I CHECK (journey_stage IS NULL OR journey_stage IN (
                    ''awareness'', ''acquisition'', ''activation'', ''engagement'',
                    ''conversion'', ''retention'', ''loyalty'', ''churned''))',
                c.table_name, c.constraint_name);
        END IF;
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'customer360.cdp_master_profiles'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%lifecycle_stage%'
          AND pg_get_constraintdef(oid) LIKE '%awareness%'
          AND pg_get_constraintdef(oid) LIKE '%churned%'
          AND pg_get_constraintdef(oid) NOT LIKE '%prospect%'
    ) THEN
        FOR c IN
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'customer360.cdp_master_profiles'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%lifecycle_stage%'
        LOOP
            EXECUTE format(
                'ALTER TABLE customer360.cdp_master_profiles DROP CONSTRAINT IF EXISTS %I',
                c.conname);
        END LOOP;

        -- cdp_master_profiles, cdp_domain_profiles, and cdp_customer_personas
        -- are FORCE RLS-protected. Scope each tenant explicitly before
        -- normalizing lifecycle_stage so non-superuser roles (e.g. managed vDB)
        -- can update every tenant's rows before the table-wide CHECK is added.
        FOR current_tenant_id IN
            SELECT tenant_id FROM customer360.sys_tenant ORDER BY tenant_id
        LOOP
            PERFORM set_config('app.tenant_id', current_tenant_id::text, true);

            UPDATE customer360.cdp_master_profiles
            SET lifecycle_stage = CASE lifecycle_stage
                WHEN 'prospect' THEN 'awareness'
                WHEN 'lead' THEN 'acquisition'
                WHEN 'customer' THEN 'conversion'
                WHEN 'vip' THEN 'loyalty'
                WHEN 'dormant' THEN 'churned'
                WHEN 'churn_risk' THEN 'churned'
                WHEN 'awareness' THEN 'awareness'
                WHEN 'acquisition' THEN 'acquisition'
                WHEN 'activation' THEN 'activation'
                WHEN 'engagement' THEN 'engagement'
                WHEN 'conversion' THEN 'conversion'
                WHEN 'retention' THEN 'retention'
                WHEN 'loyalty' THEN 'loyalty'
                WHEN 'churned' THEN 'churned'
                ELSE NULL
            END
            WHERE tenant_id = current_tenant_id
              AND lifecycle_stage IS NOT NULL
              AND lifecycle_stage NOT IN (
                  'awareness', 'acquisition', 'activation', 'engagement',
                  'conversion', 'retention', 'loyalty', 'churned'
              );

            UPDATE customer360.cdp_domain_profiles
            SET lifecycle_stage = CASE lifecycle_stage
                WHEN 'prospect' THEN 'awareness'
                WHEN 'lead' THEN 'acquisition'
                WHEN 'customer' THEN 'conversion'
                WHEN 'vip' THEN 'loyalty'
                WHEN 'dormant' THEN 'churned'
                WHEN 'churn_risk' THEN 'churned'
                ELSE lifecycle_stage
            END
            WHERE tenant_id = current_tenant_id
              AND lifecycle_stage IN ('prospect', 'lead', 'customer', 'vip', 'dormant', 'churn_risk');

            UPDATE customer360.cdp_customer_personas
            SET lifecycle_stage = CASE lifecycle_stage
                WHEN 'prospect' THEN 'awareness'
                WHEN 'lead' THEN 'acquisition'
                WHEN 'customer' THEN 'conversion'
                WHEN 'vip' THEN 'loyalty'
                WHEN 'dormant' THEN 'churned'
                WHEN 'churn_risk' THEN 'churned'
                WHEN 'awareness' THEN 'awareness'
                WHEN 'acquisition' THEN 'acquisition'
                WHEN 'activation' THEN 'activation'
                WHEN 'engagement' THEN 'engagement'
                WHEN 'conversion' THEN 'conversion'
                WHEN 'retention' THEN 'retention'
                WHEN 'loyalty' THEN 'loyalty'
                WHEN 'churned' THEN 'churned'
                ELSE NULL
            END
            WHERE tenant_id = current_tenant_id
              AND lifecycle_stage IS NOT NULL
              AND lifecycle_stage NOT IN (
                  'awareness', 'acquisition', 'activation', 'engagement',
                  'conversion', 'retention', 'loyalty', 'churned'
              );
        END LOOP;

        PERFORM set_config('app.tenant_id', '', true);

        EXECUTE 'ALTER TABLE customer360.cdp_master_profiles ADD CONSTRAINT cdp_master_profiles_lifecycle_stage_check CHECK (lifecycle_stage IN (
            ''awareness'', ''acquisition'', ''activation'', ''engagement'',
            ''conversion'', ''retention'', ''loyalty'', ''churned''))';
    END IF;

    DELETE FROM customer360.cdp_persona_config
    WHERE config_key IN (
        'LIFECYCLE_BEHAVIOR_PROSPECT_BASE',
        'LIFECYCLE_BEHAVIOR_LEAD_BASE',
        'LIFECYCLE_BEHAVIOR_CUSTOMER_BASE',
        'LIFECYCLE_BEHAVIOR_VIP_BASE',
        'LIFECYCLE_BEHAVIOR_DORMANT_BASE',
        'LIFECYCLE_BEHAVIOR_CHURN_RISK_BASE'
    );

    INSERT INTO customer360.cdp_persona_config
        (config_key, config_value, data_type, config_description, is_active, updated_by)
    VALUES
        ('LIFECYCLE_BEHAVIOR_AWARENESS_BASE', '20.0', 'NUMERIC', 'Behavior base score for awareness', TRUE, 'system_seed'),
        ('LIFECYCLE_BEHAVIOR_ACQUISITION_BASE', '40.0', 'NUMERIC', 'Behavior base score for acquisition', TRUE, 'system_seed'),
        ('LIFECYCLE_BEHAVIOR_ACTIVATION_BASE', '50.0', 'NUMERIC', 'Behavior base score for activation', TRUE, 'system_seed'),
        ('LIFECYCLE_BEHAVIOR_ENGAGEMENT_BASE', '60.0', 'NUMERIC', 'Behavior base score for engagement', TRUE, 'system_seed'),
        ('LIFECYCLE_BEHAVIOR_CONVERSION_BASE', '65.0', 'NUMERIC', 'Behavior base score for conversion', TRUE, 'system_seed'),
        ('LIFECYCLE_BEHAVIOR_RETENTION_BASE', '75.0', 'NUMERIC', 'Behavior base score for retention', TRUE, 'system_seed'),
        ('LIFECYCLE_BEHAVIOR_LOYALTY_BASE', '95.0', 'NUMERIC', 'Behavior base score for loyalty', TRUE, 'system_seed'),
        ('LIFECYCLE_BEHAVIOR_CHURNED_BASE', '30.0', 'NUMERIC', 'Behavior base score for churned', TRUE, 'system_seed')
    ON CONFLICT (config_key) DO NOTHING;
END $$;

COMMENT ON COLUMN customer360.cdp_ai_agents.journey_stage IS
    'Customer journey stage this agent serves (awareness .. churned). NULL = cross-stage. Drives the AI Agents journey map.';
COMMENT ON COLUMN customer360.crm_campaign.journey_stage IS
    'Customer journey stage this campaign targets; derived from objective in the app. Lets the journey map show live campaigns per stage.';

CREATE INDEX IF NOT EXISTS idx_cdp_ai_agents_journey_stage
    ON customer360.cdp_ai_agents (journey_stage) WHERE journey_stage IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_crm_campaign_journey_stage
    ON customer360.crm_campaign (tenant_id, journey_stage) WHERE journey_stage IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 2. AI nodes in the workflow canvas
--    ai_decide    : score/classify a profile (e.g. churn risk), result feeds a later condition node
--    ai_generate  : write subject/body/copy for a following send node
--    ai_recommend : pick product/content/next best action
--    Branching stays with the existing 'condition' node (yes/no), so crm_flow_edge,
--    crm_save_flow and crm_load_flow need no change.
-- ----------------------------------------------------------------------------
ALTER TABLE customer360.crm_flow_node
    DROP CONSTRAINT IF EXISTS crm_flow_node_node_type_check;
ALTER TABLE customer360.crm_flow_node
    ADD CONSTRAINT crm_flow_node_node_type_check CHECK (node_type IN (
        'trigger', 'condition', 'wait', 'wait_event', 'exit',
        'email', 'notification', 'push', 'update_profile', 'run_data_synch_job',
        'ai_decide', 'ai_generate', 'ai_recommend'
    )) NOT VALID;

-- agent_code is read from config, so crm_save_flow keeps shredding JSON unchanged.
ALTER TABLE customer360.crm_flow_node
    ADD COLUMN IF NOT EXISTS agent_code VARCHAR(100)
    GENERATED ALWAYS AS (config ->> 'agent_code') STORED;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'crm_flow_node_ai_agent_check'
          AND conrelid = 'customer360.crm_flow_node'::regclass
    ) THEN
        ALTER TABLE customer360.crm_flow_node
            ADD CONSTRAINT crm_flow_node_ai_agent_check
            CHECK (node_type NOT IN ('ai_decide', 'ai_generate', 'ai_recommend')
                   OR (config ->> 'agent_code' IS NOT NULL
                       AND btrim(config ->> 'agent_code') <> ''));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'crm_flow_node_agent_fk'
          AND conrelid = 'customer360.crm_flow_node'::regclass
    ) THEN
        ALTER TABLE customer360.crm_flow_node
            ADD CONSTRAINT crm_flow_node_agent_fk
            FOREIGN KEY (agent_code)
            REFERENCES customer360.cdp_ai_agents (agent_code) ON DELETE RESTRICT;
    END IF;
END $$;

COMMENT ON COLUMN customer360.crm_flow_node.agent_code IS
    'Generated from config->>agent_code. Set only for AI nodes; FK keeps every AI node pointing at a registered agent.';

CREATE INDEX IF NOT EXISTS crm_flow_node_agent_idx
    ON customer360.crm_flow_node (agent_code) WHERE agent_code IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 3. Campaign <-> agent assignment
--    autonomy: 'suggest' (advice only) or 'approve' (agent drafts, human reviews).
--    'auto' is intentionally absent: crm_review_flow_version / crm_publish_flow
--    still require a human reviewer, so allowing it here would promise too much.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customer360.crm_campaign_agent (
    campaign_agent_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id) ON DELETE CASCADE,
    campaign_id UUID NOT NULL,
    agent_code VARCHAR(100) NOT NULL
        REFERENCES customer360.cdp_ai_agents(agent_code) ON DELETE RESTRICT,
    role VARCHAR(20) NOT NULL CHECK (role IN ('plan', 'decide', 'optimize')),
    autonomy VARCHAR(20) NOT NULL DEFAULT 'approve' CHECK (autonomy IN ('suggest', 'approve')),
    -- Prompt revision pinned at campaign approval; NULL = follow cdp_ai_agents.instruction_version.
    pinned_instruction_version INTEGER CHECK (pinned_instruction_version > 0),
    guardrails JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(guardrails) = 'object'),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fk_crm_campaign_agent_tenant_campaign
        FOREIGN KEY (tenant_id, campaign_id)
        REFERENCES customer360.crm_campaign (tenant_id, campaign_id) ON DELETE CASCADE,
    CONSTRAINT uq_crm_campaign_agent UNIQUE (campaign_id, agent_code, role)
);

COMMENT ON TABLE customer360.crm_campaign_agent IS
    'Agents assigned to a campaign with a role (plan/decide/optimize), autonomy level, guardrails and pinned prompt revision. Answers "campaigns using this agent" and "agents on this campaign".';

CREATE INDEX IF NOT EXISTS idx_crm_campaign_agent_agent
    ON customer360.crm_campaign_agent (agent_code, tenant_id) WHERE is_active;

ALTER TABLE customer360.crm_campaign_agent ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.crm_campaign_agent FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.crm_campaign_agent;
CREATE POLICY tenant_policy ON customer360.crm_campaign_agent
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);

COMMIT;

-- Historical step: 018_journey_report.sql
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

DO $$
DECLARE
    c RECORD;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'customer360.cdp_journey_metric_daily'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%journey_stage%'
          AND pg_get_constraintdef(oid) LIKE '%churned%'
    ) THEN
        FOR c IN
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'customer360.cdp_journey_metric_daily'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%journey_stage%'
        LOOP
            EXECUTE format(
                'ALTER TABLE customer360.cdp_journey_metric_daily DROP CONSTRAINT IF EXISTS %I',
                c.conname);
        END LOOP;
        EXECUTE 'ALTER TABLE customer360.cdp_journey_metric_daily ADD CONSTRAINT cdp_journey_metric_daily_journey_stage_check CHECK (journey_stage IN (
            ''awareness'', ''acquisition'', ''activation'', ''engagement'',
            ''conversion'', ''retention'', ''loyalty'', ''churned''))';
    END IF;
END $$;

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
INSERT INTO customer360.sys_schema_upgrade_history (upgrade_key) VALUES ('legacy_through_018') ON CONFLICT (upgrade_key) DO NOTHING;
\else
\echo Legacy database upgrade already applied.
\endif
SELECT pg_advisory_unlock(hashtextextended('customer360.legacy_upgrade_through_018', 0));
