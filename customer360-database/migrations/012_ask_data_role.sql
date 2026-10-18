-- Restricted read-only role for "ask your data" (customer360-api: POST /ai/ask-data).
--
-- The feature runs model-written SQL, so the database, not the application, is the boundary:
--   * the role is NOLOGIN, not a superuser and NOBYPASSRLS, so row-level security still filters
--     every table by tenant (the API sets app.tenant_id on the connection);
--   * it can SELECT only the columns listed below, on the tables listed below. Personal columns
--     (names, email, phone, address, birth date, device and advertising ids, staff names and user
--     ids), JSON blobs that may carry personal data, embeddings and generated SQL are not listed.
--     A query that touches anything else, including SELECT *, is refused by Postgres.
--   * it has no write privileges.
--
-- Rules for changing this list:
--   * every table must have FORCED row-level security and a tenant_id column. The block below
--     fails the migration otherwise (cdp_persona_features, cdp_persona_history and
--     cdp_persona_score_details have neither, so they are deliberately absent);
--   * add a column only if it holds no personal data;
--   * re-running this file narrows access to exactly this list (it revokes first), and a column
--     added to a table later stays hidden until it is listed here.
--
-- The API's login role must be allowed to SET ROLE customer360_ask_data. When this migration runs
-- as that same role the grant at the end does it; otherwise run
--   GRANT customer360_ask_data TO <api login role>;
-- (a login role that is a superuser needs nothing).
BEGIN;

DO $$
DECLARE
    spec record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'customer360_ask_data') THEN
        CREATE ROLE customer360_ask_data NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
    END IF;

    GRANT USAGE ON SCHEMA customer360 TO customer360_ask_data;
    -- Start from nothing so re-running narrows access; revoking a table also revokes its column grants.
    REVOKE ALL ON ALL TABLES IN SCHEMA customer360 FROM customer360_ask_data;

    FOR spec IN
        SELECT * FROM (VALUES
            ('cdp_master_profiles', ARRAY[
                'master_profile_id', 'tenant_id', 'domain', 'current_persona_id', 'persona_name', 'persona_summary',
                'acquisition_source', 'acquisition_campaign', 'segmentation_tags', 'source_systems',
                'linked_raw_profile_count', 'customer_since', 'last_activity_at', 'preferred_channel',
                'lifecycle_stage', 'lead_conversion_probability', 'lead_grade', 'churn_probability',
                'churn_risk_tier', 'historical_clv', 'predictive_clv', 'clv_segment', 'engagement_score',
                'latest_nps_score', 'average_csat', 'overall_sentiment_score', 'profile_completeness_score',
                'identity_confidence_score', 'created_at', 'updated_at', 'status_code']),
            ('cdp_customer_personas', ARRAY[
                'persona_id', 'tenant_id', 'domain', 'master_profile_id', 'persona_archetype_id', 'match_score',
                'persona_score', 'confidence_score', 'behavior_score', 'engagement_score', 'financial_score',
                'loyalty_score', 'relationship_score', 'risk_score', 'lifecycle_stage', 'customer_value_tier',
                'risk_level', 'next_best_action', 'computed_version', 'is_active', 'computed_at', 'expires_at',
                'created_at', 'updated_at']),
            ('cdp_persona_archetypes', ARRAY[
                'persona_archetype_id', 'tenant_id', 'domain', 'persona_code', 'persona_name', 'persona_category',
                'persona_summary', 'centroid_behavior_score', 'centroid_engagement_score',
                'centroid_financial_score', 'centroid_loyalty_score', 'centroid_relationship_score',
                'centroid_risk_score', 'matched_profile_count', 'is_active', 'created_at', 'updated_at']),
            ('cdp_segments', ARRAY[
                'segment_id', 'tenant_id', 'domain', 'segment_tag', 'segment_name', 'description', 'is_active',
                'member_count', 'last_computed_at', 'created_at', 'updated_at', 'status_code']),
            ('cdp_content_items', ARRAY[
                'content_item_id', 'tenant_id', 'domain', 'item_type', 'title', 'summary', 'segment_tags',
                'published_at', 'status_code', 'created_at', 'updated_at']),
            ('crm_campaign', ARRAY[
                'campaign_id', 'tenant_id', 'segment_id', 'template_id', 'approval_status', 'strategy_summary',
                'campaign_code', 'name', 'status', 'channel', 'platform', 'objective', 'description', 'keywords',
                'lang', 'start_date', 'end_date', 'budget_amount', 'currency', 'created_at', 'updated_at']),
            ('crm_campaign_performance_daily', ARRAY[
                'performance_id', 'tenant_id', 'campaign_id', 'experiment_variant_id', 'report_date', 'spend',
                'impressions', 'clicks', 'conversions', 'revenue_estimated', 'created_at', 'updated_at']),
            ('crm_campaign_experiments', ARRAY[
                'experiment_id', 'tenant_id', 'campaign_id', 'name', 'status', 'primary_metric', 'start_date',
                'end_date', 'winning_variant_id', 'created_at', 'updated_at']),
            ('crm_campaign_experiment_variants', ARRAY[
                'variant_id', 'tenant_id', 'experiment_id', 'variant_key', 'name', 'segment_id', 'template_id',
                'allocation_percentage', 'is_control', 'status', 'created_at', 'updated_at']),
            ('crm_transactions', ARRAY[
                'transaction_id', 'tenant_id', 'master_profile_id', 'source_system', 'transaction_type',
                'transaction_status', 'entity_type', 'entity_id', 'entity_name', 'quantity', 'amount', 'currency',
                'channel', 'merchant_id', 'merchant_name', 'location_id', 'location_name', 'campaign_id',
                'campaign_name', 'transaction_time', 'created_at']),
            ('crm_opportunity', ARRAY[
                'opportunity_id', 'tenant_id', 'account_id', 'name', 'value', 'stage', 'close_date', 'description',
                'keywords', 'lang', 'created_at']),
            ('crm_account', ARRAY[
                'account_id', 'tenant_id', 'name', 'industry_id', 'description', 'keywords', 'lang', 'created_at']),
            ('crm_industry', ARRAY[
                'industry_id', 'tenant_id', 'name', 'description', 'keywords', 'lang'])
        ) AS allowed(tbl, cols)
    LOOP
        -- Tenant isolation guard: a table without forced RLS and a tenant_id would show every tenant's rows.
        IF NOT EXISTS (
            SELECT 1
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
            WHERE n.nspname = 'customer360' AND c.relname = spec.tbl
              AND c.relrowsecurity AND c.relforcerowsecurity
        ) THEN
            RAISE EXCEPTION 'ask-data role: customer360.% needs forced row-level security and a tenant_id column before it can be granted', spec.tbl;
        END IF;

        EXECUTE format(
            'GRANT SELECT (%s) ON customer360.%I TO customer360_ask_data',
            (SELECT string_agg(format('%I', col), ', ') FROM unnest(spec.cols) AS col),
            spec.tbl
        );
    END LOOP;
END
$$;

COMMENT ON ROLE customer360_ask_data IS
    'Read-only role for the ask-your-data feature: column-level SELECT on a short list of tenant-protected tables, no personal columns, no writes. See migrations/012_ask_data_role.sql.';

-- Let the role that applies this migration switch to it (a no-op for a superuser).
GRANT customer360_ask_data TO CURRENT_USER;

COMMIT;
