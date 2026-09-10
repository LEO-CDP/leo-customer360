-- ============================================================================
-- 004_email_tracking_suppression.down.sql
-- ============================================================================
-- Rollback for 004_email_tracking_suppression.sql. Idempotent. *.down.sql files
-- are excluded from the automated bootstrap and only run by hand.
-- ============================================================================

DELETE FROM customer360.cdp_event_catalog WHERE event_name IN (
    'email-delivered', 'email-bounced', 'email-complained',
    'email-opened', 'email-clicked', 'email-unsubscribed'
);

DROP TABLE IF EXISTS customer360.cdp_email_suppression;
