-- ============================================================================
-- 003_campaign_dispatch_logs.sql
-- ============================================================================
-- Forward migration for EXISTING databases (database-schema.sql creates this on
-- a fresh cluster; existing clusters never rerun it -- same convention as
-- 002_email_marketing_schema_foundation.sql). Adds the per-recipient send
-- ledger the real email_engine pipeline writes, plus the per-tenant dynamic
-- email dispatch config it reads at send time:
--   * cdp_campaign_dispatch_logs  (one row per (campaign, recipient profile))
--   * crm_email_provider_config   (per-tenant SMTP/provider config; DB source of
--                                  truth, Redis-cached by the email_engine)
--
-- Idempotency: UNIQUE (campaign_id, master_profile_id) lets the send op upsert
-- with ON CONFLICT so replaying a campaign_activation/email_engine run cannot
-- create a second dispatch row for the same recipient.
--
-- Idempotent DDL: safe to run more than once. Rollback: 003_campaign_dispatch_logs.down.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS customer360.cdp_campaign_dispatch_logs (
    dispatch_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    campaign_id UUID NOT NULL REFERENCES customer360.crm_campaign(campaign_id) ON DELETE CASCADE,
    master_profile_id UUID NOT NULL REFERENCES customer360.cdp_master_profiles(master_profile_id) ON DELETE CASCADE,
    template_id UUID REFERENCES customer360.crm_email_templates(template_id) ON DELETE SET NULL,
    recipient_email TEXT,
    -- Pending -> Sent | Failed | Skipped (ineligible: no email / opted-out) |
    -- Suppressed (on the compliance suppression list).
    status VARCHAR(50) NOT NULL DEFAULT 'Pending',
    provider VARCHAR(100),
    provider_message_id TEXT,
    rendered_subject TEXT,
    error_message TEXT,
    -- Dagster run_id of the email_engine run that produced this row (traceability
    -- back to the Dagster UI); not FK-enforced (Dagster owns run storage).
    run_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    dispatched_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT chk_cdp_campaign_dispatch_status
        CHECK (status IN ('Pending', 'Sent', 'Failed', 'Skipped', 'Suppressed')),
    CONSTRAINT uq_cdp_campaign_dispatch_recipient UNIQUE (campaign_id, master_profile_id)
);

COMMENT ON TABLE customer360.cdp_campaign_dispatch_logs IS 'Per-recipient email send ledger written by the email_engine Dagster job: one row per (campaign_id, master_profile_id) with send status, provider message id, and rendered subject. UNIQUE(campaign_id, master_profile_id) makes re-runs idempotent (ON CONFLICT DO UPDATE).';

CREATE INDEX IF NOT EXISTS idx_cdp_campaign_dispatch_tenant ON customer360.cdp_campaign_dispatch_logs (tenant_id);
CREATE INDEX IF NOT EXISTS idx_cdp_campaign_dispatch_campaign ON customer360.cdp_campaign_dispatch_logs (campaign_id);
CREATE INDEX IF NOT EXISTS idx_cdp_campaign_dispatch_campaign_status ON customer360.cdp_campaign_dispatch_logs (campaign_id, status);
CREATE INDEX IF NOT EXISTS idx_cdp_campaign_dispatch_profile ON customer360.cdp_campaign_dispatch_logs (master_profile_id);

-- --- Tenant Row-Level Security (same fail-closed policy shape as 002) --------
DO $$
BEGIN
    EXECUTE 'ALTER TABLE customer360.cdp_campaign_dispatch_logs ENABLE ROW LEVEL SECURITY;';
    EXECUTE 'ALTER TABLE customer360.cdp_campaign_dispatch_logs FORCE ROW LEVEL SECURITY;';
    EXECUTE 'DROP POLICY IF EXISTS tenant_policy ON customer360.cdp_campaign_dispatch_logs;';
    EXECUTE
        'CREATE POLICY tenant_policy ON customer360.cdp_campaign_dispatch_logs
            USING (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid)
            WITH CHECK (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid);';
END;
$$;

-- --- Per-tenant dynamic email dispatch config ------------------
-- The email_engine resolves the active row at send time (Redis-cached, DB is
-- the source of truth) instead of reading static SMTP env vars. smtp_password
-- is a secret: protect at rest (pgcrypto / secret manager) in non-dev envs.
CREATE TABLE IF NOT EXISTS customer360.crm_email_provider_config (
    config_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    name TEXT NOT NULL DEFAULT 'default',
    provider VARCHAR(50) NOT NULL DEFAULT 'mock',
    smtp_host TEXT,
    smtp_port INTEGER,
    smtp_username TEXT,
    smtp_password TEXT,
    smtp_use_tls BOOLEAN NOT NULL DEFAULT TRUE,
    from_address TEXT,
    from_name TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT chk_crm_email_provider_config_provider CHECK (provider IN ('mock', 'smtp')),
    CONSTRAINT uq_crm_email_provider_config_name UNIQUE (tenant_id, name)
);

COMMENT ON TABLE customer360.crm_email_provider_config IS 'Per-tenant email dispatch configuration: provider + SMTP connection/envelope settings resolved by the email_engine at send time (DB is source of truth, Redis-cached). At most one active row per tenant (uq_crm_email_provider_config_active).';

CREATE INDEX IF NOT EXISTS idx_crm_email_provider_config_tenant ON customer360.crm_email_provider_config (tenant_id);
-- At most one active config per tenant so the send-time lookup is unambiguous.
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_email_provider_config_active
    ON customer360.crm_email_provider_config (tenant_id) WHERE is_active;

DO $$
BEGIN
    EXECUTE 'ALTER TABLE customer360.crm_email_provider_config ENABLE ROW LEVEL SECURITY;';
    EXECUTE 'ALTER TABLE customer360.crm_email_provider_config FORCE ROW LEVEL SECURITY;';
    EXECUTE 'DROP POLICY IF EXISTS tenant_policy ON customer360.crm_email_provider_config;';
    EXECUTE
        'CREATE POLICY tenant_policy ON customer360.crm_email_provider_config
            USING (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid)
            WITH CHECK (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid);';
END;
$$;
