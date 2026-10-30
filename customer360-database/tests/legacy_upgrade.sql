-- Run only against a disposable database initialized with the canonical schema
-- and both seeds. This test deliberately mutates schema and tenant data.
\set ON_ERROR_STOP on

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM customer360.sys_schema_upgrade_history
        WHERE upgrade_key = 'legacy_through_018'
    ) THEN
        RAISE EXCEPTION 'Fresh bootstrap did not mark the legacy upgrade complete';
    END IF;
    IF (SELECT count(*) FROM customer360.cdp_segments
        WHERE segment_tag IN ('champions', 'high_value', 'growth_potential')
          AND cardinality(audience_tags) > 0) <> 3 THEN
        RAISE EXCEPTION 'Fresh seed is missing default audience tags';
    END IF;
END $$;

\ir ../legacy-upgrade.sql

DELETE FROM customer360.sys_schema_upgrade_history
WHERE upgrade_key = 'legacy_through_018';
ALTER TABLE customer360.cdp_master_profiles
    DROP CONSTRAINT cdp_master_profiles_lifecycle_stage_check;
UPDATE customer360.cdp_segments SET audience_tags = ARRAY[]::text[]
WHERE segment_tag IN ('champions', 'high_value', 'growth_potential');
UPDATE customer360.cdp_ai_agents SET status = 'INACTIVE'
WHERE agent_code = 'eligibility_policy';

INSERT INTO customer360.sys_tenant
    (tenant_id, tenant_code, tenant_name, company_name, business_type)
VALUES
    ('22222222-2222-2222-2222-222222222222', 'legacy-fixture',
     'Legacy fixture', 'Fixture', 'retail');
INSERT INTO customer360.cdp_master_profiles
    (master_profile_id, tenant_id, domain, lifecycle_stage, gender)
VALUES
    ('33333333-3333-3333-3333-333333333333',
     '11111111-1111-1111-1111-111111111111', 'unknown', 'prospect', 'female'),
    ('44444444-4444-4444-4444-444444444444',
     '22222222-2222-2222-2222-222222222222', 'unknown', 'vip', 'male');
INSERT INTO customer360.cdp_raw_profiles_stage
    (tenant_id, domain, source_system, event_payload)
VALUES
    ('22222222-2222-2222-2222-222222222222', 'unknown', 'fixture',
     '{"identity":{"anonymous_id":"anon-test","device_fingerprint":"fingerprint-test"}}'::jsonb);

DROP TRIGGER trg_invalidate_product_item_embedding ON customer360.cdp_product_items;
ALTER TABLE customer360.cdp_product_items DROP COLUMN ext_attributes;
ALTER TABLE customer360.cdp_product_items RENAME COLUMN source_id TO store_id;
ALTER TABLE customer360.cdp_customer_valuation DROP COLUMN actions;
\ir ../database-schema.sql

\ir ../legacy-upgrade.sql

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM customer360.cdp_master_profiles
        WHERE master_profile_id = '33333333-3333-3333-3333-333333333333'
          AND domain = '_' AND lifecycle_stage = 'awareness'
          AND 'demographic/gender/female' = ANY(audience_tags)
    ) OR NOT EXISTS (
        SELECT 1 FROM customer360.cdp_master_profiles
        WHERE master_profile_id = '44444444-4444-4444-4444-444444444444'
          AND domain = '_' AND lifecycle_stage = 'loyalty'
          AND 'demographic/gender/male' = ANY(audience_tags)
    ) THEN
        RAISE EXCEPTION 'Legacy profile backfills failed';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM customer360.cdp_raw_profiles_stage
        WHERE tenant_id = '22222222-2222-2222-2222-222222222222'
          AND domain = '_' AND anonymous_id = 'anon-test'
          AND device_fingerprint = 'fingerprint-test'
    ) THEN
        RAISE EXCEPTION 'Legacy tracking identifier backfill failed';
    END IF;
    IF (SELECT count(*) FROM customer360.cdp_segments
        WHERE segment_tag IN ('champions', 'high_value', 'growth_potential')
          AND cardinality(audience_tags) > 0) <> 3 THEN
        RAISE EXCEPTION 'Legacy segment audience tag backfill failed';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM customer360.cdp_ai_agents
        WHERE agent_code = 'eligibility_policy' AND status = 'ACTIVE'
    ) OR NOT EXISTS (
        SELECT 1 FROM customer360.sys_schema_upgrade_history
        WHERE upgrade_key = 'legacy_through_018'
    ) THEN
        RAISE EXCEPTION 'Legacy agent activation or upgrade marker is missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'customer360' AND table_name = 'cdp_product_items'
          AND column_name = 'source_id'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'customer360' AND table_name = 'cdp_product_items'
          AND column_name = 'ext_attributes'
    ) THEN
        RAISE EXCEPTION 'Legacy product columns were not upgraded';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'customer360' AND table_name = 'cdp_customer_valuation'
          AND column_name = 'actions'
    ) THEN
        RAISE EXCEPTION 'Legacy valuation columns were not upgraded';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'customer360.crm_campaign_performance_daily'::regclass
          AND conname = 'fk_campaign_performance_experiment_variant_tenant'
          AND pg_get_constraintdef(oid) LIKE '%SET NULL (experiment_variant_id)%'
    ) THEN
        RAISE EXCEPTION 'Variant deletion must not clear tenant_id';
    END IF;
END $$;

UPDATE customer360.cdp_ai_agents SET status = 'INACTIVE'
WHERE agent_code = 'eligibility_policy';
\ir ../legacy-upgrade.sql
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM customer360.cdp_ai_agents
        WHERE agent_code = 'eligibility_policy' AND status = 'INACTIVE'
    ) THEN
        RAISE EXCEPTION 'A repeated upgrade changed operator-managed agent state';
    END IF;
END $$;
