-- ============================================================================
-- 003_campaign_dispatch_logs.down.sql
-- ============================================================================
-- Rollback for 003_campaign_dispatch_logs.sql. Dropping the table removes its
-- indexes and RLS policy with it. Idempotent.
--
-- NOTE: *.down.sql files are EXCLUDED from the automated bootstrap
-- (deployments/postgres/run-sql.sh) and are only ever run by hand.
-- ============================================================================

DROP TABLE IF EXISTS customer360.cdp_campaign_dispatch_logs;
DROP TABLE IF EXISTS customer360.crm_email_provider_config;
