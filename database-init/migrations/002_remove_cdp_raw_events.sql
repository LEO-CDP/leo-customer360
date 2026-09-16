-- Event history is owned by the S3 Bronze/Silver lake.
-- This migration removes the retired PostgreSQL event ledger and its partitions.
-- Restore is performed by replaying the canonical S3 event envelope into the
-- approved serving projection; it is not a table rollback.
DROP FUNCTION IF EXISTS customer360.ensure_cdp_raw_events_partition(DATE);
DROP TABLE IF EXISTS customer360.cdp_raw_events CASCADE;