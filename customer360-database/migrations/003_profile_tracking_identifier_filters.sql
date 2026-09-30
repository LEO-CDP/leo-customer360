UPDATE customer360.cdp_raw_profiles_stage
SET
    anonymous_id = COALESCE(
        NULLIF(btrim(anonymous_id), ''),
        NULLIF(btrim(event_payload ->> 'anonymous_id'), ''),
        NULLIF(btrim(event_payload #>> '{identity,anonymous_id}'), '')
    ),
    device_fingerprint = COALESCE(
        NULLIF(btrim(device_fingerprint), ''),
        NULLIF(btrim(event_payload ->> 'device_fingerprint'), ''),
        NULLIF(btrim(event_payload #>> '{identity,device_fingerprint}'), '')
    )
WHERE event_payload IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_tenant_anonymous_id
    ON customer360.cdp_raw_profiles_stage (tenant_id, anonymous_id)
    WHERE anonymous_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_tenant_device_fingerprint
    ON customer360.cdp_raw_profiles_stage (tenant_id, device_fingerprint)
    WHERE device_fingerprint IS NOT NULL;
