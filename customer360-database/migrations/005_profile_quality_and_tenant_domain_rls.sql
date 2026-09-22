-- Profile quality, timezone, and tenant-domain RLS hardening.
-- Legacy TIMESTAMP values are interpreted as UTC during conversion.

---------------------------------------------------
-- TENANT DOMAIN ASSIGNMENTS: enforce tenant isolation
---------------------------------------------------
ALTER TABLE customer360.sys_tenant_domain ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.sys_tenant_domain FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.sys_tenant_domain;
CREATE POLICY tenant_policy ON customer360.sys_tenant_domain
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);

---------------------------------------------------
-- PROFILE HASH STATE: NULL means the legacy default FALSE
---------------------------------------------------
UPDATE customer360.cdp_master_profiles
SET is_hashed = FALSE
WHERE is_hashed IS NULL;

ALTER TABLE customer360.cdp_master_profiles
    ALTER COLUMN is_hashed SET DEFAULT FALSE,
    ALTER COLUMN is_hashed SET NOT NULL;

---------------------------------------------------
-- PROFILE SCORE RANGE CHECKS
---------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM customer360.cdp_master_profiles
        WHERE (lead_conversion_probability IS NOT NULL AND NOT (lead_conversion_probability BETWEEN 0 AND 1))
           OR (churn_probability IS NOT NULL AND NOT (churn_probability BETWEEN 0 AND 1))
           OR (identity_confidence_score IS NOT NULL AND NOT (identity_confidence_score BETWEEN 0 AND 1))
           OR (engagement_score IS NOT NULL AND NOT (engagement_score BETWEEN 0 AND 100))
           OR (overall_sentiment_score IS NOT NULL AND NOT (overall_sentiment_score BETWEEN -1 AND 1))
           OR (profile_completeness_score IS NOT NULL AND NOT (profile_completeness_score BETWEEN 0 AND 100))
    ) THEN
        RAISE EXCEPTION
            'Cannot add cdp_master_profiles score checks: existing score data is outside the documented ranges';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class r ON r.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'customer360'
          AND r.relname = 'cdp_master_profiles'
          AND c.conname = 'chk_cdp_mp_probability_ranges'
    ) THEN
        ALTER TABLE customer360.cdp_master_profiles
            ADD CONSTRAINT chk_cdp_mp_probability_ranges CHECK (
                (lead_conversion_probability IS NULL OR lead_conversion_probability BETWEEN 0 AND 1)
                AND (churn_probability IS NULL OR churn_probability BETWEEN 0 AND 1)
                AND (identity_confidence_score IS NULL OR identity_confidence_score BETWEEN 0 AND 1)
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class r ON r.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'customer360'
          AND r.relname = 'cdp_master_profiles'
          AND c.conname = 'chk_cdp_mp_score_ranges'
    ) THEN
        ALTER TABLE customer360.cdp_master_profiles
            ADD CONSTRAINT chk_cdp_mp_score_ranges CHECK (
                (engagement_score IS NULL OR engagement_score BETWEEN 0 AND 100)
                AND (overall_sentiment_score IS NULL OR overall_sentiment_score BETWEEN -1 AND 1)
                AND (profile_completeness_score IS NULL OR profile_completeness_score BETWEEN 0 AND 100)
            );
    END IF;
END;
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM customer360.cdp_domain_profiles
        WHERE engagement_score IS NOT NULL
          AND NOT (engagement_score BETWEEN 0 AND 100)
    ) THEN
        RAISE EXCEPTION
            'Cannot add cdp_domain_profiles engagement check: existing score data is outside 0..100';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class r ON r.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'customer360'
          AND r.relname = 'cdp_domain_profiles'
          AND c.conname = 'chk_cdp_domain_profiles_engagement_range'
    ) THEN
        ALTER TABLE customer360.cdp_domain_profiles
            ADD CONSTRAINT chk_cdp_domain_profiles_engagement_range
            CHECK (engagement_score IS NULL OR engagement_score BETWEEN 0 AND 100);
    END IF;
END;
$$;

---------------------------------------------------
-- PROFILE ACTIVITY/SCORE TIMESTAMPS: normalize legacy UTC values
---------------------------------------------------
DO $$
DECLARE
    timestamp_column RECORD;
BEGIN
    FOR timestamp_column IN
        SELECT *
        FROM (VALUES
            ('cdp_master_profiles', 'last_activity_at'),
            ('cdp_master_profiles', 'scores_updated_at'),
            ('cdp_domain_profiles', 'first_activity_at'),
            ('cdp_domain_profiles', 'last_activity_at')
        ) AS columns(table_name, column_name)
    LOOP
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'customer360'
              AND table_name = timestamp_column.table_name
              AND column_name = timestamp_column.column_name
              AND data_type = 'timestamp without time zone'
        ) THEN
            EXECUTE format(
                'ALTER TABLE customer360.%I ALTER COLUMN %I TYPE TIMESTAMPTZ USING %I AT TIME ZONE ''UTC''',
                timestamp_column.table_name,
                timestamp_column.column_name,
                timestamp_column.column_name
            );
        END IF;
    END LOOP;
END;
$$;
