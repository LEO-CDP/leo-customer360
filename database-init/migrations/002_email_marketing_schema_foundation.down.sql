-- ============================================================================
-- 002_email_marketing_schema_foundation.down.sql
-- ============================================================================
-- Rollback for 002_email_marketing_schema_foundation.sql. Reverses every object
-- in dependency-safe order (drop referencing tables first, then constraints,
-- columns, and finally the standalone table). Idempotent: safe to run even if
-- the forward migration was only partially applied.
--
-- NOTE: *.down.sql files are intentionally EXCLUDED from the automated
-- bootstrap (see deployments/postgres/run-sql.sh) so they are never applied as
-- part of a normal forward deploy. Run this manually to roll back.
-- ============================================================================

-- Drop the new standalone tenant-scoped tables (their RLS policies and indexes
-- are dropped with them).
DROP TABLE IF EXISTS customer360.crm_segment_sync_runs;
DROP TABLE IF EXISTS customer360.crm_campaign_content_items;

-- Revert crm_lead.
DROP INDEX IF EXISTS customer360.idx_crm_lead_lead_source;
ALTER TABLE customer360.crm_lead DROP CONSTRAINT IF EXISTS fk_crm_lead_lead_source;
ALTER TABLE customer360.crm_lead DROP COLUMN IF EXISTS lead_source_id;

-- Revert crm_campaign (drop FKs/CHECK before the columns they depend on).
DROP INDEX IF EXISTS customer360.idx_crm_campaign_segment;
DROP INDEX IF EXISTS customer360.idx_crm_campaign_template;
ALTER TABLE customer360.crm_campaign DROP CONSTRAINT IF EXISTS fk_crm_campaign_segment;
ALTER TABLE customer360.crm_campaign DROP CONSTRAINT IF EXISTS fk_crm_campaign_template;
ALTER TABLE customer360.crm_campaign DROP CONSTRAINT IF EXISTS fk_crm_campaign_approved_by;
ALTER TABLE customer360.crm_campaign DROP CONSTRAINT IF EXISTS chk_crm_campaign_approval_status;
ALTER TABLE customer360.crm_campaign
    DROP COLUMN IF EXISTS segment_id,
    DROP COLUMN IF EXISTS template_id,
    DROP COLUMN IF EXISTS approval_status,
    DROP COLUMN IF EXISTS approved_by,
    DROP COLUMN IF EXISTS approved_at,
    DROP COLUMN IF EXISTS strategy_summary,
    DROP COLUMN IF EXISTS ai_plan;

-- Finally the email template library (crm_campaign.template_id FK is gone now).
DROP TABLE IF EXISTS customer360.crm_email_templates;
