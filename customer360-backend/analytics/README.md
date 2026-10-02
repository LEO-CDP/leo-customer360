# `analytics` — Tracking-Log Analytics

The analytics code location aggregates tracking-log objects from S3-compatible
storage into tenant-scoped PostgreSQL and Redis source metrics. It is consumed
by the identity-resolution and reporting flows; it does not yet materialize
the broader Polars/Parquet customer-feature pipeline described in the root
backend architecture document.

## Dagster interface

- Job: `analytics_job`
- Schedule: `analytics_hourly_schedule`
- Current schedule: every three minutes (`*/3 * * * *`, UTC)
- Implementation: `source_analytics.tracking_log_aggregation.process_tracking_logs`

The job uses bounded source/object batches, Redis leases, and persistent
per-source checkpoints so retries do not double-count processed objects.

## Package layout

| Path | Responsibility |
| --- | --- |
| `dagster_defs.py` | Job, op, schedule, and active-run guard |
| `source_analytics/config.py` | Immutable database, Redis, S3, and batching settings |
| `source_analytics/tracking_log_aggregation.py` | Tracking-object processing and source totals |
| `source_analytics/object_store.py` | S3/MinIO object access |
| `source_analytics/repositories.py` | PostgreSQL source-state persistence |
| `tests/` | Aggregation and component tests |

## Configuration

The service reads `.env` and environment variables including `DB_*`,
`REDIS_*`, `S3_*`, `ANALYTICS_S3_ENDPOINT_URL`,
`ANALYTICS_DATA_SOURCE_LIMIT`, `ANALYTICS_MAX_WORKERS`,
`ANALYTICS_SOURCE_BATCH_SIZE`, `ANALYTICS_OBJECT_BATCH_SIZE`, and
`ANALYTICS_EVENT_RAW_PREFIX`.

## Testing

Run from the repository root:

```bash
python -m pytest customer360-backend/analytics/tests -q
```