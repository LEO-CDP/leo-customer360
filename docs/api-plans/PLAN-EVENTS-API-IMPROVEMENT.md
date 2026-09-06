# Events API Improvement Plan (TODO)

## 1) Why Events APIs are missing create/import today

Current behavior is intentional in this codebase:
- Events router is read-only and explicitly says ingestion is out-of-band.
- API docs mark Events endpoints as read-only.

Evidence:
- customer360-api/core/routers/events_api.py: read-only docstring and list/get-only intent.
- docs/customer360-api.md: Events documented as read-only stream.

## 2) Target behavior you want

Business rules to enforce:
- cdp_raw_events.user_id can be NULL (pipeline/system ingestion).
- cdp_raw_events.master_profile_id can be NULL initially.
- cdp_raw_events.raw_profile_id must be NOT NULL and always linked to cdp_raw_profiles_stage.
- Events can be created via API or imported from CSV/JSON (local files and S3).
- Events can be ingested with hashed email/hashed phone, then linked to raw_profiles_stage.
- After CID merge, master_profile_id is backfilled on cdp_raw_events.

## 3) Gaps between current and target

Schema/model mismatch:
- (resolved) database-schema.sql now defines cdp_raw_events.raw_profile_id as NOT NULL.
- (resolved) customer360-api/core/models/events.py now maps raw_profile_id as required/non-nullable.

CID gap:
- identity_resolution resolver links raw profile -> master profile, but does not backfill cdp_raw_events.master_profile_id by raw_profile_id.

API gap:
- No POST /events endpoint.
- No import endpoint for CSV/JSON or S3 URI.
- No import job tracking endpoint.

## 4) Implementation TODO (recommended sequence)

### Phase A - Contract and schema hardening
- [x] Add API contract doc for EventCreate and EventImport payloads.
- [x] update database-schema.sql: cdp_raw_events.raw_profile_id -> NOT NULL.
- [x] Keep cdp_raw_events.master_profile_id nullable.
- [x] Add optional idempotency key for ingestion safety:
  - [x] event_dedup_key text
  - [x] unique index on (tenant_id, source_system, event_dedup_key) where event_dedup_key is not null.
- [x] Add check constraint for minimal identity in EventCreate input (at least one of: raw_profile_id, hashed email, hashed phone, external_customer_id, device_id, advertising_id, cookie_id, session_id).

### Phase B - Event write APIs
- [x] Add POST /api/v1/events/ (single create).
- [x] Add POST /api/v1/events/bulk (small/medium batch create).
- [x] Behavior for create:
  - [x] If raw_profile_id is provided: verify it exists and tenant/domain compatible.
  - [x] If raw_profile_id missing but identity provided (including hashed email/phone): create/find cdp_raw_profiles_stage row first, then set event.raw_profile_id.
  - [x] Insert cdp_raw_events with master_profile_id = null by default unless resolved immediately.
- [x] Keep user_id optional and default null for non-interactive ingestion.

### Phase C - File import APIs (local + S3)
- [ ] Add POST /api/v1/events/import/file (multipart upload: csv/json/jsonl). Uploaded file must be less than 10MB
- [ ] Add POST /api/v1/events/import/uri (s3://..., file://..., absolute local path if enabled).
- [ ] Add GET /api/v1/events/import-jobs/{job_id} (status/progress/errors).
- [ ] Add validation and normalization pipeline:
  - strict column mapping
  - datetime parsing
  - domain/category validation
  - numeric and currency normalization
  - per-row error collection (dead-letter rows)
- [ ] Add chunked ingestion with bounded transaction sizes.

### Phase D - CID backfill integration
- [ ] Extend identity_resolution resolver:
  - after linking raw_profile_id -> master_profile_id,
  - run update on cdp_raw_events where tenant_id + raw_profile_id + master_profile_id is null,
  - set master_profile_id to resolved value.
- [ ] Ensure idempotent SQL and safe retries.
- [ ] Add optional periodic reconciliation job for historical events missed before rollout.

### Phase E - Security, tenancy, and operations
- [ ] Enforce tenant scope from auth context; do not trust arbitrary tenant_id in payload when caller is tenant-scoped.
- [ ] Keep RLS-compatible behavior (set app.tenant_id in DB session).
- [ ] Add rate limits and payload size limits for imports.
- [ ] Add audit records for import jobs and bulk writes.
- [ ] Add metrics: rows accepted/rejected, import duration, backfill count, unresolved-event count.

### Phase F - Tests and acceptance criteria
- [x] Unit tests for EventCreate validation and raw-profile linking behavior.
- [ ] Integration tests:
  - [x] create event with explicit raw_profile_id,
  - [x] create event with hashed email/phone (auto create/find raw profile),
  - import CSV and JSON,
  - CID run updates master_profile_id on existing events.
- [ ] Multi-tenant isolation tests for all new endpoints.
- [ ] Performance tests for bulk and import paths.

## 5) Suggested API request shapes

Single create (example):
- POST /api/v1/events/
- Body fields:
  - tenant_id, domain, source_system, channel, event_category, event_name, event_time
  - one of:
    - raw_profile_id
    - identity payload: email/phone_number (hashed allowed), external_customer_id, device_id, advertising_id, cookie_id, session_id
  - optional: user_id, event_value, currency, entity_type, entity_id, event_payload

Import from S3 (example):
- POST /api/v1/events/import/uri
- Body fields:
  - tenant_id, source_uri, format (csv|json|jsonl), source_system, domain, dry_run

## 6) Rollout strategy

- [ ] Release 1: POST /events + CID backfill update + tests.
- [ ] Release 2: bulk/import endpoints + import job tracking.
- [ ] Release 3: reconciliation job + advanced observability + dedup optimization.

Release 1 progress (dev):
- [x] POST /events implemented.
- [ ] CID backfill update still pending (Phase D).
- [x] EventCreate + raw-profile linking tests added and passing.

## 7) Definition of done

- Event creation is possible via API and file imports.
- Every stored event has non-null raw_profile_id linked to cdp_raw_profiles_stage.
- master_profile_id is eventually populated after CID.
- Hashed identity ingestion works end-to-end without exposing plaintext PII.
- New endpoints are tenant-safe, tested, and documented.
