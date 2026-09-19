-- Replace the email-only outbound connector table with the generic activation
-- connector registry. sys_data_source remains the inbound collection model.

CREATE TABLE IF NOT EXISTS customer360.crm_connector_config (
    connector_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id) ON DELETE CASCADE,
    user_id UUID REFERENCES customer360.sys_user(user_id) ON DELETE SET NULL,
    name TEXT NOT NULL DEFAULT 'default',
    connector_type VARCHAR(50) NOT NULL,
    provider VARCHAR(100) NOT NULL,
    direction VARCHAR(20) NOT NULL DEFAULT 'OUTBOUND',
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    endpoint_url TEXT,
    region VARCHAR(100),
    auth_type VARCHAR(50),
    credentials_ref TEXT,
    credentials JSONB NOT NULL DEFAULT '{}'::jsonb,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    capabilities JSONB DEFAULT '{}'::jsonb,
    last_tested_at TIMESTAMP WITH TIME ZONE,
    last_test_status VARCHAR(20),
    last_error TEXT,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT uq_crm_connector_name UNIQUE (tenant_id, name),
    CONSTRAINT chk_crm_connector_type CHECK (connector_type IN ('EMAIL', 'SMS', 'PUSH', 'CHAT', 'ADS', 'WEBHOOK')),
    CONSTRAINT chk_crm_connector_direction CHECK (direction IN ('INBOUND', 'OUTBOUND', 'BIDIRECTIONAL')),
    CONSTRAINT chk_crm_connector_status CHECK (status IN ('ACTIVE', 'INACTIVE', 'ERROR', 'TESTING')),
    CONSTRAINT chk_crm_connector_last_test_status CHECK (
        last_test_status IS NULL OR last_test_status IN ('SUCCESS', 'FAILED')
    ),
    CONSTRAINT chk_crm_connector_credentials_object CHECK (jsonb_typeof(credentials) = 'object'),
    CONSTRAINT chk_crm_connector_config_object CHECK (jsonb_typeof(config) = 'object'),
    CONSTRAINT chk_crm_connector_capabilities_object CHECK (
        capabilities IS NULL OR jsonb_typeof(capabilities) = 'object'
    )
);

CREATE INDEX IF NOT EXISTS idx_crm_connector_tenant
    ON customer360.crm_connector_config (tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_connector_channel
    ON customer360.crm_connector_config (tenant_id, connector_type);
CREATE INDEX IF NOT EXISTS idx_crm_connector_status
    ON customer360.crm_connector_config (tenant_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_connector_default
    ON customer360.crm_connector_config (tenant_id, connector_type)
    WHERE is_default = TRUE AND is_active = TRUE;

DO $$
BEGIN
    IF to_regclass('customer360.crm_email_provider_config') IS NOT NULL THEN
        -- The legacy SMTP password is intentionally not copied into JSONB. Set
        -- credentials_ref (or configure a secret-manager-backed credential)
        -- before enabling production dispatch for migrated connectors.
        INSERT INTO customer360.crm_connector_config (
            connector_id,
            tenant_id,
            name,
            connector_type,
            provider,
            direction,
            status,
            auth_type,
            config,
            is_default,
            is_active,
            metadata,
            created_at,
            updated_at
        )
        SELECT
            config_id,
            tenant_id,
            name,
            'EMAIL',
            UPPER(provider),
            'OUTBOUND',
            CASE WHEN is_active THEN 'ACTIVE' ELSE 'INACTIVE' END,
            CASE WHEN smtp_username IS NOT NULL THEN 'USERNAME_PASSWORD' END,
            jsonb_strip_nulls(jsonb_build_object(
                'smtp_host', smtp_host,
                'smtp_port', smtp_port,
                'smtp_username', smtp_username,
                'smtp_use_tls', smtp_use_tls,
                'from_address', from_address,
                'from_name', from_name
            )),
            is_active,
            is_active,
            metadata,
            created_at,
            updated_at
        FROM customer360.crm_email_provider_config
        ON CONFLICT (tenant_id, name) DO NOTHING;

        DROP TABLE customer360.crm_email_provider_config;
    END IF;
END;
$$;

ALTER TABLE customer360.crm_connector_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.crm_connector_config FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.crm_connector_config;
CREATE POLICY tenant_policy ON customer360.crm_connector_config
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);

COMMENT ON TABLE customer360.crm_connector_config IS
'Outbound CRM connector configuration for activation and communication channels such as email, SMS, push, chat, ads and webhooks. Inbound data collection remains modeled by sys_data_source.';
