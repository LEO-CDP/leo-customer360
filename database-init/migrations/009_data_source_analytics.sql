-- Persist source-level analytics snapshots on raw and resolved profiles.

ALTER TABLE customer360.cdp_raw_profiles_stage
    ADD COLUMN IF NOT EXISTS data_source_analytics JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE customer360.cdp_master_profiles
    ADD COLUMN IF NOT EXISTS data_source_analytics JSONB NOT NULL DEFAULT '{}'::jsonb;
