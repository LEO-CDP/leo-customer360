-- AI Campaign Draft Review (specs/002-ai-campaign-draft-creation).
-- Idempotent forward migration for environments already initialized from an
-- older database-schema.sql snapshot. The crm_campaign segment/template/
-- approval_status columns and the crm_campaign_content_items relation table
-- are already covered by 002_email_marketing_schema_foundation.sql; this
-- migration adds the reviewer approve/reject decision table and the
-- updated_at column that are unique to the AI campaign draft feature
-- (updated_at backs CampaignDraftRepository's optimistic-concurrency guard).

ALTER TABLE customer360.crm_campaign
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT now();

CREATE TABLE IF NOT EXISTS customer360.crm_campaign_reviews (
    review_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    campaign_id UUID NOT NULL REFERENCES customer360.crm_campaign(campaign_id) ON DELETE CASCADE,
    reviewer_id UUID NOT NULL REFERENCES customer360.sys_user(user_id),
    decision VARCHAR(10) NOT NULL CHECK (decision IN ('approve', 'reject')),
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE customer360.crm_campaign_reviews IS 'One row per reviewer approve/reject decision on a campaign draft (FR-010).';

CREATE INDEX IF NOT EXISTS idx_crm_campaign_reviews_campaign ON customer360.crm_campaign_reviews (campaign_id);

-- Row-Level Security (crm_campaign already has RLS from
-- migrations/001_harden_tenant_rls_policies.sql).
DO $$
BEGIN
    EXECUTE 'ALTER TABLE customer360.crm_campaign_reviews ENABLE ROW LEVEL SECURITY;';
    EXECUTE 'ALTER TABLE customer360.crm_campaign_reviews FORCE ROW LEVEL SECURITY;';
    EXECUTE 'DROP POLICY IF EXISTS tenant_policy ON customer360.crm_campaign_reviews;';
    EXECUTE
        'CREATE POLICY tenant_policy ON customer360.crm_campaign_reviews
            USING (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid)
            WITH CHECK (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid);';
END;
$$;
