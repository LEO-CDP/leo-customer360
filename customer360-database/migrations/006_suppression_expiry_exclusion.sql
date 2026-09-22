-- Allow expired active suppressions to be replaced without weakening active-window uniqueness.

CREATE EXTENSION IF NOT EXISTS btree_gist;

DROP INDEX IF EXISTS customer360.uq_crm_suppression_global;
DROP INDEX IF EXISTS customer360.uq_crm_suppression_campaign;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class r ON r.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'customer360'
          AND r.relname = 'crm_suppression_list'
          AND c.conname = 'ex_crm_suppression_global_window'
    ) THEN
        ALTER TABLE customer360.crm_suppression_list
            ADD CONSTRAINT ex_crm_suppression_global_window
            EXCLUDE USING gist (
                tenant_id WITH =,
                channel WITH =,
                identifier_type WITH =,
                identifier WITH =,
                tstzrange(
                    created_at,
                    COALESCE(expires_at, 'infinity'::timestamptz),
                    '[)'
                ) WITH &&
            )
            WHERE (status = 'ACTIVE' AND scope = 'GLOBAL');
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class r ON r.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'customer360'
          AND r.relname = 'crm_suppression_list'
          AND c.conname = 'ex_crm_suppression_campaign_window'
    ) THEN
        ALTER TABLE customer360.crm_suppression_list
            ADD CONSTRAINT ex_crm_suppression_campaign_window
            EXCLUDE USING gist (
                tenant_id WITH =,
                channel WITH =,
                identifier_type WITH =,
                identifier WITH =,
                campaign_id WITH =,
                tstzrange(
                    created_at,
                    COALESCE(expires_at, 'infinity'::timestamptz),
                    '[)'
                ) WITH &&
            )
            WHERE (status = 'ACTIVE' AND scope = 'CAMPAIGN');
    END IF;
END;
$$;
