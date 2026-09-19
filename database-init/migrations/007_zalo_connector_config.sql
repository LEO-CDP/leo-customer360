-- Move legacy Zalo OA settings/tokens into the generic outbound connector registry.
-- Existing connector rows win, so a partially migrated deployment is safe to rerun.

INSERT INTO customer360.crm_connector_config (
    tenant_id,
    name,
    connector_type,
    provider,
    direction,
    status,
    credentials,
    config,
    is_default,
    is_active
)
SELECT
    ds.tenant_id,
    'zalo',
    'CHAT',
    'ZALO',
    'BIDIRECTIONAL',
    CASE WHEN ds.status = 1 THEN 'ACTIVE' ELSE 'INACTIVE' END,
    jsonb_strip_nulls(jsonb_build_object(
        'oa_id', ds.access_tokens ->> 'oa_id',
        'app_id', ds.access_tokens ->> 'app_id',
        'access_token', ds.access_tokens ->> 'access_token',
        'refresh_token', ds.access_tokens ->> 'refresh_token',
        'token_expires_at', ds.access_tokens ->> 'token_expires_at',
        'app_secret', ds.security_code
    )),
    jsonb_build_object(
        'oa_api_base_url', COALESCE(ds.data_source_url, 'https://openapi.zalo.me'),
        'oauth_authorize_url', 'https://oauth.zaloapp.com/v4/oa/permission',
        'oa_token_url', 'https://oauth.zaloapp.com/v4/oa/access_token',
        'oauth_redirect_uri', '',
        'token_refresh_cron', '*/30 * * * *',
        'dispatch_adapter', 'mock',
        'zns_api_base_url', 'https://business.openapi.zalo.me',
        'batch_size', 500,
        'optout_projection_cron', '*/15 * * * *',
        'optout_lookback_hours', 6
    ),
    ds.status = 1,
    ds.status = 1
FROM customer360.sys_data_source ds
WHERE ds.slug = 'zalo-oa'
ON CONFLICT (tenant_id, name) DO NOTHING;

COMMENT ON TABLE customer360.crm_connector_config IS
'Outbound CRM connector configuration. CHAT/ZALO credentials and runtime settings are stored in the credentials/config JSONB columns; inbound collection remains modeled by sys_data_source.';