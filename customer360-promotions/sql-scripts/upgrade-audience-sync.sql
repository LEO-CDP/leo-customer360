-- Idempotent upgrade for installations that already have the leo_ads schema.
CREATE TABLE IF NOT EXISTS leo_ads.customer360_tenant_map (
    customer360_tenant_id UUID PRIMARY KEY,
    tenant_id BIGINT NOT NULL UNIQUE REFERENCES leo_ads.tenant(tenant_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_customer360_tenant_map_source_destination
    ON leo_ads.customer360_tenant_map (customer360_tenant_id, tenant_id);
ALTER TABLE leo_ads.audience ADD COLUMN IF NOT EXISTS customer360_tenant_id UUID;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'leo_ads.audience'::regclass
          AND conname = 'fk_audience_customer360_tenant'
    ) THEN
        ALTER TABLE leo_ads.audience ADD CONSTRAINT fk_audience_customer360_tenant
            FOREIGN KEY (customer360_tenant_id, tenant_id)
            REFERENCES leo_ads.customer360_tenant_map (customer360_tenant_id, tenant_id);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_audience_tenant_id
    ON leo_ads.audience (tenant_id, audience_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_user_serving_key_tenant_hash
    ON leo_ads.user_serving_key (tenant_id, user_key_hash);

CREATE TABLE IF NOT EXISTS leo_ads.audience_member (
    tenant_id BIGINT NOT NULL,
    audience_id BIGINT NOT NULL,
    user_key_hash BYTEA NOT NULL,
    PRIMARY KEY (tenant_id, audience_id, user_key_hash),
    FOREIGN KEY (tenant_id, audience_id)
        REFERENCES leo_ads.audience (tenant_id, audience_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, user_key_hash)
        REFERENCES leo_ads.user_serving_key (tenant_id, user_key_hash) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_audience_member_user
    ON leo_ads.audience_member (tenant_id, user_key_hash);
