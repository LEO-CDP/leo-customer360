-- Existing databases do not rerun database-schema.sql automatically. Recreate
-- tenant policies so an unset/blank app.tenant_id fails closed instead of
-- raising an invalid UUID cast error.
DO $$
DECLARE
    table_name TEXT;
    tenant_tables TEXT[] := ARRAY[
        'sys_organization',
        'sys_user',
        'sys_role',
        'sys_audit_log',
        'crm_campaign',
        'crm_campaign_member',
        'crm_lead',
        'crm_lead_source',
        'crm_contact',
        'crm_account',
        'crm_opportunity',
        'crm_industry',
        'crm_customer_contacts',
        'crm_transactions',
        'cdp_master_profiles',
        'cdp_raw_profiles_stage',
        'cdp_profile_links',
        'cdp_identity_index',
        'cdp_profile_merge_history',
        'cdp_raw_events',
        'cdp_relations',
        'cdp_domain_profiles',
        'cdp_segments',
        'cdp_content_items',
        'cdp_customer_personas',
        'cdp_persona_archetypes'
    ];
BEGIN
    FOREACH table_name IN ARRAY tenant_tables LOOP
        IF to_regclass(format('customer360.%I', table_name)) IS NULL THEN
            CONTINUE;
        END IF;

        EXECUTE format('ALTER TABLE customer360.%I ENABLE ROW LEVEL SECURITY;', table_name);
        EXECUTE format('ALTER TABLE customer360.%I FORCE ROW LEVEL SECURITY;', table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_policy ON customer360.%I;', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_policy ON customer360.%I
                USING (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid)
                WITH CHECK (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid);',
            table_name
        );
    END LOOP;
END;
$$;