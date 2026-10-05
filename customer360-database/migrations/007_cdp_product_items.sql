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
