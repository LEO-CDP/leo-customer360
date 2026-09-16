# Full Analytics With S3: Phase 0 Baseline

> Snapshot date: 2026-09-15
>
> Scope: Phase 0 baseline and contract, plus the Phase 1 Bronze ingestion
> implementation in `data-tracking-api`.

## Baseline Snapshot

The local PostgreSQL and Redis services were healthy when this snapshot was
taken. Values below are aggregate-only and contain no event payloads.

| Measure | Local value | Source |
|---|---:|---|
| Legacy PostgreSQL event ledger count before S3 cutover | 7,706 | Historical baseline |
| Oldest legacy event time before S3 cutover | 2025-09-14 17:06:12.214493+00 | Historical PostgreSQL baseline |
| Newest legacy event time before S3 cutover | 2026-09-14 12:06:13.165638+00 | Historical PostgreSQL baseline |
| Redis tracking stream length | 0 | `XLEN data-tracking:events` |
| Redis pending tracking messages | 0 | `XPENDING data-tracking:events s3-writers` |
| S3 tracking bucket/object/byte totals | unavailable | local client had no S3 credentials |

The S3 row is intentionally marked unavailable. Before enabling manifest
writes or production cutover, capture the same aggregate from the configured
MinIO/S3 endpoint with credentials that can list only the event buckets.

## Confirmed Current Boundaries

- `data-tracking-api` validates identity-bearing batches, enriches each event,
  and hands them to either Redis Streams or the bounded in-process queue.
- Redis acknowledges and deletes a message only after the S3 write returns.
- Bronze objects are immutable batches; retries reuse deterministic event and
  batch IDs and carry compressed bytes safely through Redis.
- Analytics currently discovers per-source hourly objects and updates Redis
  counters plus `sys_data_source` totals.
- The legacy `customer360-api` `/events` write API has been removed. Profile
  analytics still has a PostgreSQL raw-event read dependency that must move to
  the S3 Silver query path before the table can be dropped.

## Approved Phase 1 Contract

- Bronze format: `jsonl.gz`, UTF-8 JSON Lines compressed with gzip.
- Bronze key during migration: `s3://data-tracking-<source-id>/YYYY-MM-DD-HH/<batch-id>.jsonl.gz`.
- Each record includes `schema_version`, `ingestion_version`, `event_id`,
  `data_source_id`, UTC `event_time`, UTC `received_at`, `event_name`,
  `event_category`, `event_dedup_key`, an `identity` object, and `payload`.
- Existing upstream `event_id` values are preserved. Otherwise the service
  derives a stable UUID from the source deduplication key or a canonical
  fallback hash. The fallback includes the event position in its batch.
- A retry with the same ordered event IDs reuses the same batch object key.
  Bronze writes remain at-least-once; Silver compaction must deduplicate by
  `event_id`, then `event_dedup_key`, then the documented fallback hash.
- S3 metadata records event count, schema version, ingestion version, and the
  SHA-256 checksum of the compressed object.
- MinIO stores one `_processed/<object-id>.json` marker per raw object. The
  marker contains the object key, checksum, event count, source ID, and stored
  status. Redis idempotency keys suppress duplicate publication during their
  TTL; the MinIO marker remains the recovery authority after Redis expiry or a
  worker restart.
- The public tracking API has no PostgreSQL dependency or credentials. The
  current per-source bucket layout remains until the environment-level
  tenant/source key migration is approved.
- Silver format: Parquet. Batch analytics should use the existing Polars path;
  a higher-concurrency query service remains a later phase decision.

## Rollout Gate

Verify that the tracking runtime can write only its source bucket's `events/`
and `_processed/` prefixes, and that its Redis stream/idempotency keys are
bounded. No PostgreSQL migration or database role is required by the public
tracking service.

The remaining Phase 0 environment approvals are retention durations for UAT
and PROD, S3 versioning/object-lock requirements, and the named rollback
owner. They are deployment policy decisions rather than values that can be
derived safely from this repository.