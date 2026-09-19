-- Preserve the concrete connector lineage for raw profiles so master-profile
-- lists can filter by a tenant's selected data source.

ALTER TABLE customer360.cdp_raw_profiles_stage
    ADD COLUMN IF NOT EXISTS data_source_id UUID;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_cdp_raw_profiles_stage_data_source'
          AND conrelid = 'customer360.cdp_raw_profiles_stage'::regclass
    ) THEN
        ALTER TABLE customer360.cdp_raw_profiles_stage
            ADD CONSTRAINT fk_cdp_raw_profiles_stage_data_source
            FOREIGN KEY (data_source_id)
            REFERENCES customer360.sys_data_source(data_source_id);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_data_source
    ON customer360.cdp_raw_profiles_stage (tenant_id, data_source_id)
    WHERE data_source_id IS NOT NULL;