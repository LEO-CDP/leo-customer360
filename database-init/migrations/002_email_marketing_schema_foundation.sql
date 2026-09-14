-- ============================================================================
-- 002_email_marketing_schema_foundation.sql
-- ============================================================================
-- Forward migration for EXISTING databases (database-schema.sql only creates
-- these on a fresh cluster; existing clusters never rerun it -- see
-- 001_harden_tenant_rls_policies.sql). Adds the relational structure behind the
-- agentic outbound email marketing flow:
--   * crm_email_templates                (AI-authored, human-approved templates)
--   * crm_campaign  + segment/template/approval/AI-planning columns
--   * crm_lead      + lead_source_id relation
--   * crm_campaign_content_items         (campaign <-> cdp_content_items)
--   * crm_segment_sync_runs              (segment-sync audit with route counts)
-- plus indexes and tenant RLS policies for the new tenant-scoped tables.
--
-- Idempotent: safe to run more than once. Rollback: 002_email_marketing_schema_foundation.down.sql
-- ============================================================================

-- --- Email template library ------------------------------------------------
CREATE TABLE IF NOT EXISTS customer360.crm_email_templates (
    template_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    name TEXT NOT NULL,
    subject TEXT,
    html_body TEXT,
    text_body TEXT,
    variables JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(50) NOT NULL DEFAULT 'Draft',
    created_by UUID REFERENCES customer360.sys_user(user_id),
    approved_by UUID REFERENCES customer360.sys_user(user_id),
    approved_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT chk_crm_email_templates_status
        CHECK (status IN ('Draft', 'InReview', 'Approved', 'Rejected'))
);

COMMENT ON TABLE customer360.crm_email_templates IS 'Email template library for the agentic outbound flow: subject/html/text body plus declared merge variables, authored (often by an AI agent) as Draft and moved through InReview -> Approved/Rejected before a campaign can use it.';

CREATE INDEX IF NOT EXISTS idx_crm_email_templates_tenant ON customer360.crm_email_templates (tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_email_templates_tenant_status ON customer360.crm_email_templates (tenant_id, status);

-- --- crm_campaign: segment/template links, approval gate, AI planning -------
ALTER TABLE customer360.crm_campaign
    ADD COLUMN IF NOT EXISTS segment_id UUID,
    ADD COLUMN IF NOT EXISTS template_id UUID,
    ADD COLUMN IF NOT EXISTS approval_status VARCHAR(50) DEFAULT 'Draft',
    ADD COLUMN IF NOT EXISTS approved_by UUID,
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS strategy_summary TEXT,
    ADD COLUMN IF NOT EXISTS ai_plan JSONB;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_crm_campaign_segment' AND conrelid = 'customer360.crm_campaign'::regclass) THEN
        ALTER TABLE customer360.crm_campaign ADD CONSTRAINT fk_crm_campaign_segment
            FOREIGN KEY (segment_id) REFERENCES customer360.cdp_segments(segment_id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_crm_campaign_template' AND conrelid = 'customer360.crm_campaign'::regclass) THEN
        ALTER TABLE customer360.crm_campaign ADD CONSTRAINT fk_crm_campaign_template
            FOREIGN KEY (template_id) REFERENCES customer360.crm_email_templates(template_id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_crm_campaign_approved_by' AND conrelid = 'customer360.crm_campaign'::regclass) THEN
        ALTER TABLE customer360.crm_campaign ADD CONSTRAINT fk_crm_campaign_approved_by
            FOREIGN KEY (approved_by) REFERENCES customer360.sys_user(user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_crm_campaign_approval_status' AND conrelid = 'customer360.crm_campaign'::regclass) THEN
        ALTER TABLE customer360.crm_campaign ADD CONSTRAINT chk_crm_campaign_approval_status
            CHECK (approval_status IN ('Draft', 'InReview', 'Approved', 'Rejected'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_crm_campaign_segment ON customer360.crm_campaign (segment_id);
CREATE INDEX IF NOT EXISTS idx_crm_campaign_template ON customer360.crm_campaign (template_id);

-- --- crm_lead: lead_source_id relation -------------------------------------
ALTER TABLE customer360.crm_lead
    ADD COLUMN IF NOT EXISTS lead_source_id UUID;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_crm_lead_lead_source' AND conrelid = 'customer360.crm_lead'::regclass) THEN
        ALTER TABLE customer360.crm_lead ADD CONSTRAINT fk_crm_lead_lead_source
            FOREIGN KEY (lead_source_id) REFERENCES customer360.crm_lead_source(lead_source_id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_crm_lead_lead_source ON customer360.crm_lead (lead_source_id);

-- --- Campaign <-> content-item relation ------------------------------------
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

COMMENT ON TABLE customer360.crm_campaign_content_items IS 'Relation table linking a crm_campaign to the cdp_content_items it uses, with ordering (position) and role; unique per (campaign_id, content_item_id) so re-planning a campaign cannot duplicate content links.';

CREATE INDEX IF NOT EXISTS idx_crm_campaign_content_tenant ON customer360.crm_campaign_content_items (tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_campaign_content_campaign ON customer360.crm_campaign_content_items (campaign_id);
CREATE INDEX IF NOT EXISTS idx_crm_campaign_content_item ON customer360.crm_campaign_content_items (content_item_id);

-- --- Segment-sync audit trail ----------------------------------------------
CREATE TABLE IF NOT EXISTS customer360.crm_segment_sync_runs (
    sync_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    segment_id UUID NOT NULL REFERENCES customer360.cdp_segments(segment_id) ON DELETE CASCADE,
    triggered_by UUID REFERENCES customer360.sys_user(user_id),
    status VARCHAR(50) NOT NULL DEFAULT 'Pending',
    dry_run BOOLEAN NOT NULL DEFAULT FALSE,
    matched_count INTEGER NOT NULL DEFAULT 0,
    customer_count INTEGER NOT NULL DEFAULT 0,
    lead_count INTEGER NOT NULL DEFAULT 0,
    contact_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    finished_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB,
    CONSTRAINT chk_crm_segment_sync_runs_status
        CHECK (status IN ('Pending', 'Running', 'Completed', 'Failed'))
);

COMMENT ON TABLE customer360.crm_segment_sync_runs IS 'Audit record for each segment -> CRM sync execution: routing-bucket counts (customer/lead/contact), skipped/error counts, dry-run flag, and timing, produced by the sync engine.';

CREATE INDEX IF NOT EXISTS idx_crm_segment_sync_runs_tenant ON customer360.crm_segment_sync_runs (tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_segment_sync_runs_segment ON customer360.crm_segment_sync_runs (segment_id);
CREATE INDEX IF NOT EXISTS idx_crm_segment_sync_runs_tenant_started ON customer360.crm_segment_sync_runs (tenant_id, started_at DESC);

-- --- Tenant Row-Level Security for the new tenant-scoped tables -------------
-- Same fail-closed policy shape used across the schema: an unset/blank
-- app.tenant_id denies all rows rather than erroring on an invalid UUID cast.
DO $$
DECLARE
    t TEXT;
    new_tables TEXT[] := ARRAY[
        'crm_email_templates',
        'crm_campaign_content_items',
        'crm_segment_sync_runs'
    ];
BEGIN
    FOREACH t IN ARRAY new_tables LOOP
        EXECUTE format('ALTER TABLE customer360.%I ENABLE ROW LEVEL SECURITY;', t);
        EXECUTE format('ALTER TABLE customer360.%I FORCE ROW LEVEL SECURITY;', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_policy ON customer360.%I;', t);
        EXECUTE format(
            'CREATE POLICY tenant_policy ON customer360.%I
                USING (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid)
                WITH CHECK (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid);',
            t
        );
    END LOOP;
END;
$$;
