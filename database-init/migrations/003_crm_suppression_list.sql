-- Replace the email-only suppression table with the omnichannel CRM
-- suppression registry. Existing email suppressions are migrated as global or
-- campaign-scoped EMAIL rows.

CREATE TABLE IF NOT EXISTS customer360.crm_suppression_list (
    suppression_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id) ON DELETE CASCADE,
    master_profile_id UUID REFERENCES customer360.cdp_master_profiles(master_profile_id) ON DELETE SET NULL,
    channel VARCHAR(30) NOT NULL,
    identifier_type VARCHAR(30) NOT NULL,
    identifier TEXT NOT NULL,
    reason VARCHAR(50) NOT NULL,
    scope VARCHAR(20) NOT NULL DEFAULT 'GLOBAL',
    campaign_id UUID REFERENCES customer360.crm_campaign(campaign_id) ON DELETE CASCADE,
    source VARCHAR(100),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    expires_at TIMESTAMP WITH TIME ZONE,
    removed_at TIMESTAMP WITH TIME ZONE,
    removed_by UUID REFERENCES customer360.sys_user(user_id) ON DELETE SET NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT chk_crm_suppression_list_channel CHECK (
        channel IN ('EMAIL', 'SMS', 'PUSH', 'WHATSAPP', 'ZALO', 'VOICE', 'GLOBAL')
    ),
    CONSTRAINT chk_crm_suppression_list_identifier_type CHECK (
        identifier_type IN ('EMAIL', 'PHONE', 'PUSH_TOKEN', 'DEVICE_ID', 'PROFILE_ID')
    ),
    CONSTRAINT chk_crm_suppression_list_reason CHECK (
        reason IN (
            'HARD_BOUNCE', 'SOFT_BOUNCE', 'COMPLAINT', 'UNSUBSCRIBE', 'MANUAL',
            'INVALID_DESTINATION', 'POLICY', 'LEGAL'
        )
    ),
    CONSTRAINT chk_crm_suppression_list_scope CHECK (scope IN ('GLOBAL', 'CAMPAIGN')),
    CONSTRAINT chk_crm_suppression_list_status CHECK (status IN ('ACTIVE', 'REMOVED')),
    CONSTRAINT chk_crm_suppression_list_campaign_scope CHECK (
        (scope = 'GLOBAL' AND campaign_id IS NULL)
        OR (scope = 'CAMPAIGN' AND campaign_id IS NOT NULL)
    ),
    CONSTRAINT chk_crm_suppression_list_removed CHECK (
        (status = 'ACTIVE' AND removed_at IS NULL)
        OR (status = 'REMOVED' AND removed_at IS NOT NULL)
    ),
    CONSTRAINT chk_crm_suppression_list_identifier_not_blank CHECK (btrim(identifier) <> ''),
    CONSTRAINT chk_crm_suppression_list_email_lower CHECK (
        identifier_type <> 'EMAIL' OR identifier = lower(identifier)
    ),
    CONSTRAINT chk_crm_suppression_list_profile_identifier CHECK (
        (identifier_type = 'PROFILE_ID' AND channel = 'GLOBAL')
        OR (identifier_type <> 'PROFILE_ID' AND channel <> 'GLOBAL')
    ),
    CONSTRAINT chk_crm_suppression_list_metadata CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_crm_suppression_tenant_status
    ON customer360.crm_suppression_list (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_crm_suppression_lookup
    ON customer360.crm_suppression_list (
        tenant_id, channel, identifier_type, identifier, status, expires_at
    );
CREATE INDEX IF NOT EXISTS idx_crm_suppression_profile
    ON customer360.crm_suppression_list (tenant_id, master_profile_id)
    WHERE master_profile_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_suppression_global
    ON customer360.crm_suppression_list (tenant_id, channel, identifier_type, identifier)
    WHERE status = 'ACTIVE' AND scope = 'GLOBAL';
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_suppression_campaign
    ON customer360.crm_suppression_list (tenant_id, channel, identifier_type, identifier, campaign_id)
    WHERE status = 'ACTIVE' AND scope = 'CAMPAIGN';

DO $$
BEGIN
    IF to_regclass('customer360.cdp_email_suppression') IS NOT NULL THEN
        INSERT INTO customer360.crm_suppression_list (
            suppression_id,
            tenant_id,
            channel,
            identifier_type,
            identifier,
            reason,
            scope,
            campaign_id,
            source,
            status,
            metadata,
            created_at,
            updated_at
        )
        SELECT
            suppression_id,
            tenant_id,
            'EMAIL',
            'EMAIL',
            lower(btrim(email)),
            CASE lower(reason)
                WHEN 'hard_bounce' THEN 'HARD_BOUNCE'
                WHEN 'complaint' THEN 'COMPLAINT'
                WHEN 'unsubscribe' THEN 'UNSUBSCRIBE'
                WHEN 'manual' THEN 'MANUAL'
                ELSE 'MANUAL'
            END,
            CASE WHEN campaign_id IS NULL THEN 'GLOBAL' ELSE 'CAMPAIGN' END,
            campaign_id,
            source,
            'ACTIVE',
            COALESCE(metadata, '{}'::jsonb),
            created_at,
            created_at
        FROM customer360.cdp_email_suppression
        ON CONFLICT DO NOTHING;

        DROP TABLE customer360.cdp_email_suppression;
    END IF;
END;
$$;

ALTER TABLE customer360.crm_suppression_list ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.crm_suppression_list FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.crm_suppression_list;
CREATE POLICY tenant_policy ON customer360.crm_suppression_list
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);

COMMENT ON TABLE customer360.crm_suppression_list IS
'Omnichannel CRM suppression registry used by activation services before message dispatch. Supports global and campaign-scoped suppression across Email, SMS, Push, WhatsApp, Zalo, Voice and profile-level suppression.';
