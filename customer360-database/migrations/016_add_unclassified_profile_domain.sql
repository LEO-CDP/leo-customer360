-- Add the neutral profile-domain fallback used when tracking data has no
-- reliable business-domain classification.

INSERT INTO customer360.sys_domain (
    domain_code,
    domain_name,
    description,
    display_order,
    is_system,
    is_active
)
VALUES (
    '_',
    'Unclassified / Not set',
    'Profile has no reliable business-domain classification.',
    0,
    TRUE,
    TRUE
)
ON CONFLICT (domain_code) DO UPDATE
SET domain_name = EXCLUDED.domain_name,
    description = EXCLUDED.description,
    display_order = EXCLUDED.display_order,
    is_system = TRUE,
    is_active = TRUE,
    updated_at = NOW();

DO $$
DECLARE
    current_tenant_id UUID;
BEGIN
    FOR current_tenant_id IN
        SELECT tenant_id FROM customer360.sys_tenant ORDER BY tenant_id
    LOOP
        -- sys_tenant_domain is FORCE RLS-protected. Scope each tenant
        -- operation explicitly instead of relying on a superuser bypass.
        PERFORM set_config('app.tenant_id', current_tenant_id::text, true);

        INSERT INTO customer360.sys_tenant_domain (
            tenant_id,
            domain_id,
            is_default,
            is_active
        )
        SELECT
            current_tenant_id,
            domain_id,
            FALSE,
            TRUE
        FROM customer360.sys_domain
        WHERE domain_code = '_'
        ON CONFLICT (tenant_id, domain_id) DO UPDATE
        SET is_active = TRUE;

        UPDATE customer360.cdp_master_profiles
        SET domain = '_',
            updated_at = NOW()
        WHERE tenant_id = current_tenant_id
          AND (domain IS NULL OR BTRIM(domain) = '' OR domain = 'unknown');

        UPDATE customer360.cdp_raw_profiles_stage
        SET domain = '_'
        WHERE tenant_id = current_tenant_id
          AND (domain IS NULL OR BTRIM(domain) = '' OR domain = 'unknown');
    END LOOP;

    -- Do not leak a tenant context to statements after this migration.
    PERFORM set_config('app.tenant_id', '', true);
END;
$$;

ALTER TABLE customer360.cdp_master_profiles
    ALTER COLUMN domain SET DEFAULT '_';

ALTER TABLE customer360.cdp_raw_profiles_stage
    ALTER COLUMN domain SET DEFAULT '_';
