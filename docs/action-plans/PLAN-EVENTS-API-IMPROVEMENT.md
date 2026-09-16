# Event Ingestion Migration Note

The legacy `customer360-api` `/events` and `/events/bulk` write API has been
removed. New behavioral-event writers must use `data-tracking-api`:

- `POST /api/v1/tracking/logs` for public SDK/webhook ingestion.
- The authenticated internal compatibility endpoint only when a trusted
  service needs to bridge an existing writer into the same contract.

The tracking service validates and normalizes the envelope, publishes to Redis
Streams, and writes immutable gzip JSONL to S3/MinIO under `events/`. Durable
raw-object state is stored under `_processed/`. The public tracking service
never connects to PostgreSQL.

## Remaining Migration Work

- Route any remaining external connector or legacy service writers through the
  canonical tracking contract.
- Preserve `cdp_raw_profiles_stage` resolution in the owning authenticated
  service; it must not become a reason for the public tracking service to open a
  database connection.
- Build the S3 Silver compaction and tenant-safe query path before removing
  PostgreSQL raw-event readers.
- Backfill historical PostgreSQL events month by month, reconcile counts and
  checksums, and make replay resumable before the retention/rollback window
  expires.
- Remove the retired PostgreSQL event ledger only through the Phase 6 archive,
  reconciliation, dependency-scan, and replay gates in
  `FULL-ANALYTICS-WITH-S3.md`.

The old event-write API contract, import endpoints, and CID backfill plan are
not active interfaces and must not be reintroduced as PostgreSQL write paths.
