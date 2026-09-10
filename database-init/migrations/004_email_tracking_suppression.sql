-- ============================================================================
-- 004_email_tracking_suppression.sql
-- ============================================================================
-- Forward migration for EXISTING databases (database-schema.sql /
-- init-core-database.sql cover fresh clusters). Adds the compliance suppression
-- list and the governed email-engagement event vocabulary:
--   * cdp_email_suppression   (hard-bounce / complaint / unsubscribe -> do not mail)
--   * cdp_event_catalog rows  (email-delivered/bounced/complained/opened/clicked/
--                              unsubscribed) so email events normalize into the
--                              existing cdp_raw_events store.
--
-- Idempotent: safe to run more than once. Rollback: 004_email_tracking_suppression.down.sql
-- ============================================================================

-- --- Compliance suppression list -------------------------------------------
CREATE TABLE IF NOT EXISTS customer360.cdp_email_suppression (
    suppression_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    email TEXT NOT NULL,
    reason VARCHAR(50) NOT NULL,
    campaign_id UUID REFERENCES customer360.crm_campaign(campaign_id) ON DELETE SET NULL,
    source VARCHAR(100),
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT chk_cdp_email_suppression_reason
        CHECK (reason IN ('hard_bounce', 'complaint', 'unsubscribe', 'manual'))
);

COMMENT ON TABLE customer360.cdp_email_suppression IS 'Compliance suppression list: an email here is never sent again for the tenant. Populated on hard bounce / spam complaint / unsubscribe; enforced by the email_engine eligibility query. Unique per (tenant, lower(email)).';

CREATE INDEX IF NOT EXISTS idx_cdp_email_suppression_tenant ON customer360.cdp_email_suppression (tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cdp_email_suppression_email
    ON customer360.cdp_email_suppression (tenant_id, lower(email));

DO $$
BEGIN
    EXECUTE 'ALTER TABLE customer360.cdp_email_suppression ENABLE ROW LEVEL SECURITY;';
    EXECUTE 'ALTER TABLE customer360.cdp_email_suppression FORCE ROW LEVEL SECURITY;';
    EXECUTE 'DROP POLICY IF EXISTS tenant_policy ON customer360.cdp_email_suppression;';
    EXECUTE
        'CREATE POLICY tenant_policy ON customer360.cdp_email_suppression
            USING (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid)
            WITH CHECK (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid);';
END;
$$;

-- --- Governed email engagement event vocabulary ----------------------------
INSERT INTO customer360.cdp_event_catalog
    (event_name, event_category, domain_scope, description, is_conversion_default, value_field, display_order)
VALUES
    ('email-delivered',    'GENERAL', 'all', 'Marketing email was accepted/delivered by the recipient MTA.', FALSE, NULL, 300),
    ('email-bounced',      'GENERAL', 'all', 'Marketing email bounced (hard/soft).',                          FALSE, NULL, 310),
    ('email-complained',   'GENERAL', 'all', 'Recipient marked the marketing email as spam/complaint.',       FALSE, NULL, 320),
    ('email-opened',       'GENERAL', 'all', 'Recipient opened the marketing email (tracking pixel).',        FALSE, NULL, 330),
    ('email-clicked',      'GENERAL', 'all', 'Recipient clicked a link in the marketing email.',              FALSE, NULL, 340),
    ('email-unsubscribed', 'GENERAL', 'all', 'Recipient unsubscribed via the email footer link.',             FALSE, NULL, 350)
ON CONFLICT (event_name) DO NOTHING;
