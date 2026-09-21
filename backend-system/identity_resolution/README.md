# Customer Identity Resolution (CIR)

Customer Identity Resolution (CIR) consumes raw customer profiles from the PostgreSQL staging table, matches them to tenant-scoped master profiles, consolidates identity data, and optionally recomputes the resulting persona. Resolved profiles can then be projected to S3 as master-profile event documents.

This service runs inside `backend-system` and is scheduled by Dagster. The default poll interval is 10 minutes.

## Responsibilities

CIR owns these steps:

1. Read active matching and consolidation rules from `cdp_profile_attributes`.
2. Select unprocessed rows from `cdp_raw_profiles_stage` per tenant.
3. Match each raw profile to an existing master profile or create a new one.
4. Write links and consolidated values to the Customer 360 schema.
5. Mark successfully handled staging rows with `status_code = 3`.
6. Recompute the resolved master profile persona when enabled.
7. Build S3 event projections for the master profiles changed during the run.

CIR is not the ingestion API. Tracking and ingestion services should validate and enqueue raw events; CIR processes the database staging queue asynchronously.

## Package Layout

| Path | Responsibility |
| --- | --- |
| `identity_resolution/cir_tasks.py` | Runtime configuration, Redis lease coordination, bounded batch drain, S3 projection orchestration, and targeted persona-count refreshes. This is the scheduled task entry point. |
| `identity_resolution/resolver.py` | Matching, master-profile creation/linking, consolidation, RLS tenant switching, and per-batch commits. |
| `identity_resolution/models.py` | Domain models used by the resolver. |
| `identity_resolution/persona_engine.py` | Persona resolution for a matched master profile. |
| `identity_resolution/persona.py` | Persona naming and profile helpers. |
| `identity_resolution/profile_event_projection.py` | Loads matching raw profiles, scans source event objects, and writes master-profile event JSON to the configured store. |
| `identity_resolution/rls.py` | PostgreSQL tenant context helpers. |
| `dagster_defs.py` | Dagster op, job, sensor, and targeted-run configuration. |
| `worker.py` | Legacy-compatible in-process worker that executes the Dagster job repeatedly. Prefer Dagster daemon scheduling for production. |
| `healthcheck.py` | PostgreSQL connectivity probe used by the container health check. |
| `scripts/init_sample_data.py` | Idempotent demo-rule and sample-data setup. |
| `tests/test_cir_tasks.py` | Unit tests for lease handling, batch limits, and projection aggregation. |
| `tests/test_resolver.py` | Resolver and matching behavior tests. |

## Runtime Flow

```text
Dagster sensor
  dagster_defs.py:identity_resolution_poll_sensor()
	|
	| every CIR_POLL_INTERVAL_SECONDS (default: 600)
	| skips when dagster_defs.py:_identity_resolution_run_active() is true
	v
Dagster job
  dagster_defs.py:identity_resolution_job()
	|
	v
Dagster op
  dagster_defs.py:resolve_identities_op()
	|
	+--> targeted branch when persona_archetype_id is configured
	|      cir_tasks.py:recompute_persona_archetype_match_count()
	|      `-- updates one tenant-scoped archetype count and returns
	|
	`--> normal staging-drain branch
	       cir_tasks.py:run_identity_resolution_tasks()
			 |
			 +--> cir_tasks.py:build_redis_client()
			 |      `-- creates the Redis client
			 |
			 +--> shared/redis_lock.py:acquire_redis_lease()
			 |      `-- claims identity-resolution:staging-drain-lock
			 |
			 +--> psycopg2.connect()
			 |      `-- opens one PostgreSQL connection for the run
			 |
			 +--> cir_tasks.py:_drain_resolution_batches()
			 |      |
			 |      +--> resolver.py:
			 |      |      CustomerIdentityResolver.run_resolution_batch()
			 |      |      |
			 |      |      +--> _get_active_rules()
			 |      |      +--> _fetch_tenant_ids()
			 |      |      +--> _fetch_unprocessed_profiles()
			 |      |      +--> _find_master_profile()
			 |      |      +--> _link_and_update() or
			 |      |      |    _create_master_and_link()
			 |      |      +--> PersonaResolutionEngine.resolve_persona()
			 |      |      +--> _mark_as_processed()
			 |      |      `-- commit one database transaction
			 |      |
			 |      +--> cir_tasks.py:_merge_resolved_profiles()
			 |      |      `-- deduplicates changed master IDs by tenant
			 |      |
			 |      `--> lease.refresh() after each full batch
			 |
			 +--> cir_tasks.py:_project_resolved_profiles()
			 |      |
			 |      `--> profile_event_projection.py:
			 |             MasterProfileEventProjector.project_profiles()
			 |             |
			 |             +--> _load_matchers()
			 |             +--> _iter_source_objects()
			 |             +--> _iter_object()
			 |             +--> _match_event()
			 |             `--> MasterProfileEventStore.put_events()
			 |                    `-- one pass per tenant per run
			 |
			 +--> conn.close()
			 `--> lease.release()
```

### Normal scheduled run

1. `identity_resolution_poll_sensor()` in `dagster_defs.py` evaluates every `CIR_POLL_INTERVAL_SECONDS`. It yields a `RunRequest` only when no `identity_resolution_job` run is queued or active.
2. `identity_resolution_job()` invokes `resolve_identities_op()`.
3. `resolve_identities_op()` calls `run_identity_resolution_tasks()` in `identity_resolution/cir_tasks.py` when no targeted persona configuration is supplied.
4. `run_identity_resolution_tasks()` acquires the Redis lease before opening PostgreSQL. If another worker owns the lease, it returns `0` without connecting to the database.
5. `_drain_resolution_batches()` calls `CustomerIdentityResolver.run_resolution_batch()` repeatedly. The resolver updates `last_resolved_profiles_by_tenant` after each batch; the task runner merges those IDs into one set per tenant.
6. Each resolver batch sets the RLS context, processes tenant rows, commits on success, and rolls back on failure. The task loop refreshes the Redis lease after every full batch.
7. Once all batches are committed, `_project_resolved_profiles()` calls `MasterProfileEventProjector.project_profiles()` once per changed tenant. This avoids repeating matcher queries and S3 object scans for tenants represented in multiple batches.
8. The PostgreSQL connection closes and the Redis lease releases in the task function's `finally` block.

### Batch stop conditions

- A run processes at most `MAX_BATCHES_PER_RUN * BATCH_SIZE` raw profiles.
- A batch returning fewer than `BATCH_SIZE` rows ends the drain because the staging queue is currently empty for the configured work scan.
- Reaching `MAX_BATCHES_PER_RUN` ends the current run and leaves remaining rows for the next sensor tick.
- A resolver exception rolls back the current batch, releases the run resources, and is reported to Dagster for retry handling.

### Targeted persona refresh

When both `tenant_id` and `persona_archetype_id` are supplied in the Dagster op config, `resolve_identities_op()` bypasses staging-table processing and calls `recompute_persona_archetype_match_count()` in `identity_resolution/cir_tasks.py`. That function sets the tenant context, counts distinct active `cdp_customer_personas.master_profile_id` values, updates the requested archetype, commits, and returns the new count. It does not acquire the staging-drain Redis lease.

## Entry Points

### Scheduled CIR drain

Use the task function below for cron, Airflow, Dagster assets, or maintenance scripts:

```python
from identity_resolution.cir_tasks import run_identity_resolution_tasks

processed = run_identity_resolution_tasks()
```

The function returns the number of raw profiles processed. It returns `0` when another process currently owns the Redis lease or when there is no work.

Run the module directly from the `backend-system` directory:

```bash
python -m identity_resolution.cir_tasks
```

### Dagster

Start the Dagster code location from `backend-system`:

```bash
dagster dev -w workspace.yaml
```

The job is named `identity_resolution_job`; the sensor is named `identity_resolution_poll_sensor`. The sensor is enabled by default and submits at most one active or queued run at a time.

A normal run executes the full staging drain. A targeted run can refresh one persona archetype without draining staging:

```json
{
	"ops": {
		"resolve_identities_op": {
			"config": {
				"tenant_id": "<tenant UUID>",
				"persona_archetype_id": "<archetype UUID>"
			}
		}
	}
}
```

`tenant_id` is required when `persona_archetype_id` is supplied.

### Worker compatibility mode

`worker.py` executes the Dagster job in-process and sleeps between cycles. It is useful for local or legacy container deployments:

```bash
python worker.py
```

Production deployments should use the Dagster daemon and sensor so runs, retries, and history remain visible in Dagster.

## Configuration

Values are read from environment variables. `.env` is loaded by `cir_tasks.py` for local execution.

| Variable | Default | Description |
| --- | --- | --- |
| `DB_HOST` | `localhost` | PostgreSQL host. |
| `DB_NAME` | `cdp` | PostgreSQL database name. |
| `DB_USER` | `postgres` | PostgreSQL user. |
| `DB_PASSWORD` | `postgres` | PostgreSQL password. |
| `DB_PORT` | `5432` | PostgreSQL port. |
| `DB_SCHEMA` | `customer360` | Schema containing CIR tables. |
| `CIR_BATCH_SIZE` | `500` | Maximum raw profiles processed per resolver batch; clamped to `1..5000`. |
| `CIR_MAX_BATCHES_PER_RUN` | `10` | Maximum batches processed by one scheduled run. |
| `REDIS_HOST` | `localhost` | Redis host used for the distributed lease. |
| `REDIS_PORT` | `6580` | Redis port. |
| `REDIS_DB` | `0` | Redis logical database. |
| `REDIS_PASSWORD` | unset | Optional Redis password. |
| `CIR_LOCK_TTL_SECONDS` | `3600` | Lease lifetime. It must exceed the expected longest run or be refreshed by the batch loop. |
| `CIR_POLL_INTERVAL_SECONDS` | `600` | Dagster sensor and compatibility-worker interval in seconds. |
| `DAGSTER_HOME` | Dagster default | Persistent Dagster run-storage directory. Set this in deployed environments. |

Do not put tenant IDs in global configuration. Tenant isolation is enforced by query predicates and PostgreSQL RLS context switching.

## Data and Transaction Guarantees

- Every resolver batch begins with a database connection and commits its own changes. A failure rolls back the current batch and is re-raised to Dagster.
- Only staging rows selected as unprocessed are handled; successfully handled rows are marked with `status_code = 3`.
- The resolver sets the PostgreSQL tenant context before tenant-scoped reads and writes. Preserve this whenever adding queries.
- The Redis lease prevents overlapping drain runs across worker processes or containers. The lease is released in `finally` and refreshed after every full batch.
- Projection occurs after resolver batches have committed. Changed master IDs are deduplicated per tenant, so a tenant is projected once per scheduled run instead of once per batch.
- Projection failures occur after the resolver batch commits. They are visible as run failures and can be retried; they do not roll back already committed identity-resolution work.

## Performance Notes

The principal scaling controls are `CIR_BATCH_SIZE`, `CIR_MAX_BATCHES_PER_RUN`, and the database indexes used by the staging, link, and tenant columns. Increase batch size only after checking database lock duration and memory use.

`run_identity_resolution_tasks()` intentionally aggregates master IDs while draining. The projection module loads matchers and scans source objects, so calling it once per tenant per run avoids repeated database reads and object-store scans when the same tenant appears in multiple batches.

When investigating slow runs, measure these phases separately:

1. Time spent in `CustomerIdentityResolver.run_resolution_batch()`.
2. Number and size of batches per run.
3. Number of changed master profiles per tenant.
4. Time spent in `MasterProfileEventProjector.project_profiles()` and source-object listing.
5. Redis lease contention and Dagster queueing.

Do not increase the lease TTL as a substitute for diagnosing a slow batch. A lease that expires during processing can allow overlapping work.

## Local Setup

From the repository root, start the project dependencies using the repository's normal development scripts, then run:

```bash
cd backend-system/identity_resolution
./run_tests.sh
```

The test script creates or reuses `.venv`, installs `requirements.txt`, installs the local `customer360-dao` checkout, loads the repository `.env` when present, and runs pytest.

For a quick CIR demo, initialize sample rules and data first:

```bash
python scripts/init_sample_data.py
python -m identity_resolution.cir_tasks
```

The demo setup must always be scoped to its configured demo tenant. Do not use it against a shared environment without reviewing the cleanup statements.

## Testing Strategy

Run the focused orchestration tests during changes to `cir_tasks.py`:

```bash
.venv/bin/python -m pytest -q tests/test_cir_tasks.py
```

Run the full package suite before merging:

```bash
./run_tests.sh
```

Important coverage areas:

- Redis lease acquisition, refresh, and release.
- Skip behavior when another worker owns the lease.
- Batch budget and stop conditions.
- Deduplication and one-time projection per tenant.
- Resolver matching, consolidation, tenant isolation, rollback, and idempotency.
- Dagster job wiring and targeted persona refresh configuration.

When adding a new runtime behavior, add a unit test at the owning layer. Keep Dagster tests focused on wiring; keep matching and SQL behavior in resolver tests.

## Safe Change Guide

### Add or change matching behavior

1. Update the metadata contract or rule model first.
2. Modify `resolver.py` and preserve tenant predicates and RLS context.
3. Add resolver tests for positive, negative, duplicate, and multi-tenant cases.
4. Run `tests/test_resolver.py` and then the full package suite.

### Change scheduling or throughput

1. Modify `cir_tasks.py` or `dagster_defs.py`.
2. Keep the Redis lease around the complete drain and projection operation.
3. Preserve the bounded-run behavior so one scheduled invocation cannot run forever.
4. Add or update orchestration tests for batch limits and lease behavior.
5. Check Dagster sensor behavior to ensure it does not queue overlapping runs.

### Change event projection

1. Modify `profile_event_projection.py`.
2. Preserve tenant-scoped matcher queries and stable event de-duplication.
3. Test missing buckets, compressed and uncompressed JSONL, malformed timestamps, and repeated event IDs.
4. Verify S3 configuration through `leo-customer360-dao` settings.

### Change database schema

Update the canonical schema/migrations in `database-init/` and keep resolver SQL, indexes, and documentation aligned. Do not hide schema changes in runtime initialization code except for the explicitly defensive demo setup.

## Operational Troubleshooting

| Symptom | First checks |
| --- | --- |
| Runs are skipped | Check Redis connectivity, `CIR_LOCK_KEY` ownership/TTL, and whether another Dagster run is active. |
| No profiles are processed | Check `cdp_raw_profiles_stage` for the tenant, `status_code`, active rules in `cdp_profile_attributes`, and database/RLS permissions. |
| A run reaches its limit every time | Inspect batch duration and queue size; tune batch size or max batches only after measuring. |
| Identity links are correct but S3 is stale | Inspect projection logs, source bucket names, DAO settings, and object-list permissions. Database commits happen before projection. |
| Dagster cannot import the package | Start from `backend-system` or use the configured workspace; `dagster_defs.py` adds the identity-resolution directory to `sys.path` for workspace loading. |
| Health check fails | Test PostgreSQL connectivity with the same `DB_*` values and verify the database is accepting connections. |

## Ownership Rules

- Keep business logic in services, not Dagster definitions or shell scripts.
- Validate public inputs and raise explicit errors for invalid targeted runs.
- Preserve `tenant_id` in every SQL statement and never expose cross-tenant data.
- Avoid adding dependencies unless the existing package cannot provide the behavior.
- Keep scheduled work bounded, observable, and retryable.
- Update this README and focused tests when changing runtime contracts, environment variables, or operational behavior.