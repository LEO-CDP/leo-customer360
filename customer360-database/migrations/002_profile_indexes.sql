-- The profile activity endpoints filter by master_profile_id and date. Tenant
-- is also applied by PostgreSQL RLS, so keep it first for selective scans.
DROP INDEX IF EXISTS customer360.idx_contacts_master_profile_date;

CREATE INDEX IF NOT EXISTS idx_contacts_tenant_master_date ON customer360.crm_customer_contacts (
    tenant_id,
    master_profile_id,
    contact_date DESC
)
WHERE
    master_profile_id IS NOT NULL
    AND contact_date IS NOT NULL;

-- Supports engagement/channel aggregates and timeline transaction ordering
-- without relying on the less selective master_profile_id-only index.
CREATE INDEX IF NOT EXISTS idx_transactions_tenant_master_time ON customer360.crm_transactions (
    tenant_id,
    master_profile_id,
    transaction_time DESC
)
WHERE
    master_profile_id IS NOT NULL
    AND transaction_time IS NOT NULL;


-- Indexes for the master-profile list date filters and default ordering.
CREATE INDEX IF NOT EXISTS idx_cdp_mp_tenant_activity_sort ON customer360.cdp_master_profiles (
    tenant_id,
    last_activity_at DESC NULLS LAST,
    updated_at DESC NULLS LAST,
    created_at DESC,
    master_profile_id ASC
);

CREATE INDEX IF NOT EXISTS idx_cdp_mp_tenant_last_activity ON customer360.cdp_master_profiles (
    tenant_id,
    last_activity_at DESC,
    master_profile_id ASC
)
WHERE
    last_activity_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cdp_mp_tenant_updated_at ON customer360.cdp_master_profiles (
    tenant_id,
    updated_at DESC,
    master_profile_id ASC
)
WHERE
    updated_at IS NOT NULL;