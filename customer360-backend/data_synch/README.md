# `data_synch` — Data Synchronization Scaffold

This code location is currently a runnable Dagster placeholder for future
external and inbound data synchronization pipelines. It does not yet ingest
data or write to the Customer 360 database.

## Dagster interface

- Job: `data_synch_job`
- Op: `data_synch_placeholder_op`
- Behavior: logs start/end and sleeps for
  `DATA_SYNCH_PLACEHOLDER_SLEEP_SECONDS` seconds (default `2`)

The implementation is in `dagster_defs.py`. Future work may add CRM or
warehouse imports into `cdp_raw_profiles_stage` and outbound synchronization
of resolved profiles.

## Dependencies and local development

The service currently requires only Dagster, as declared in
`requirements.txt`. From `customer360-backend/`, load it with:

```bash
dagster dev -w workspace.yaml
```