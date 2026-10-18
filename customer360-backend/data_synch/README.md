# `data_synch` — Campaign Audience Synchronization

This code location exports approved campaign segment members to configured
third-party custom audiences. It supports Meta/Facebook Ads, Google Ads
Customer Match, and TikTok Ads.

## Dagster interface

- Job: `data_synch_job`
- Op: `run_data_synch_op`
- Audit table: `customer360.crm_ad_sync_runs`
- Identifiers are normalized and SHA-256 hashed in memory. Raw email and phone
  values are never sent to a provider or written to the audit table.
- Only active profiles with a valid email or phone and no active
  global/campaign suppression are eligible. Provider-specific consent keys
  should be added as an explicit connector policy rather than inferred here.
- Retries reuse the same tenant/campaign/segment/platform/audience idempotency
  key and preserve the external audience ID.

Run config:

```yaml
ops:
  run_data_synch_op:
    config:
      tenant_id: "<uuid>"
      campaign_id: "<uuid>"
      platform: "meta_ads" # meta_ads | google_ads | tiktok_ads
      segment_id: "<uuid>" # must match the campaign audience
      audience_name: "Spring retention"
      dry_run: false
      batch_size: 1000
```

The tenant must have an active `crm_connector_config` row with
`connector_type = 'ADS'`. Provider-specific non-secret settings belong in
`config`; access tokens and developer tokens belong in `credentials`.

Meta requires `ad_account_id` and `access_token`. Google Ads requires
`customer_id`, `user_list_resource`, `developer_token`, and `access_token`.
TikTok requires `advertiser_id` and `access_token`. Endpoint/API-version
overrides are supported through connector `config` for provider versioning.

## Dependencies and local development

The service currently requires only Dagster, as declared in
`requirements.txt`. From `customer360-backend/`, load it with:

```bash
dagster dev -w workspace.yaml
```