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
