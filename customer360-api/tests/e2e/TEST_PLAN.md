# E2E Test Plan — SCRUM-93 / 94 / 97 / 98

Traceable plan mapping **every acceptance criterion** of both tickets to concrete
test cases across all test types, with the execution method, expected result, and
**data-cleanup** strategy for each. The automated suite lives beside this file
(`tests/e2e/`); cases not automatable via the public API are called out with where
they *are* covered (unit tests / DB smoke) so AC coverage is 100% and honest.

- **SCRUM-93** — Schema & Migration Foundation
- **SCRUM-94** — Segment-ID Driven CRM Sync Engine

**Run a case by its id below:** `CASES="S94-02 R-A1" ./test.sh` (space-separated; a
prefix like `CASES=S94` runs the whole group; `./test.sh` alone runs everything).
Each automated test is tagged with `@pytest.mark.case("<id>")` matching the **ID**
column, so the ids here are the selectors.

## 1. Environment & auth

| | |
|---|---|
| Target | UAT — `https://beta.leocdp.com/c360api` (Caddy → API, `root_path=/c360api`) |
| Auth | Keycloak (`/auth`, realm `customer360`, client `customer360-api`) password grant → Bearer JWT with `tenant_id`/`user_id`/roles. SSO-off targets: `X-Tenant-Id`/`X-User-Id` headers. |
| Tenant | `11111111-1111-1111-1111-111111111111` (shared UAT test tenant) |
| Runner | `./test.sh` (loads `tests/e2e/.env`, mints a fresh token, runs pytest) |
| Config | `tests/e2e/.env` (git-ignored) — see `README.md` |

## 2. Test types covered

Positive (happy path) · Negative (guards/validation) · Boundary (empty/zero/limits) ·
Idempotency (replay) · Tenant isolation · Security/AuthZ · Data integrity (FK/CHECK/RLS) ·
Audit/observability · Dry-run vs real.

## 3. Data-safety & cleanup strategy

The suite runs against a **shared** UAT tenant, so every case cleans up after itself:

1. **Ephemeral fixtures** (segments, campaigns, content-items) are created with an
   `e2e-` tag/name prefix and registered with a per-test `track` fixture that
   `DELETE`s them in teardown (LIFO, tolerant of already-deleted), **even on failure**.
2. **Segment delete cascades** its `crm_segment_sync_runs` rows (`ON DELETE CASCADE`),
   so audit rows never linger.
3. **Default suite writes no `crm_*` target rows**: it uses an *impossible* segment
   predicate (`engagement_score < -1`) → `matched = 0`. Contract, dry-run, idempotency,
   audit, guards, isolation and schema round-trips are all validated with zero footprint.
4. **Routing tests** (`test_routing_e2e.py`) that *do* write to `crm_lead`/`crm_contact`/
   `crm_customer_contacts`/`crm_transactions`/`crm_lead_source` are **opt-in**
   (`E2E_ALLOW_DATA_WRITES=1`) and self-clean by computing the sync's **deterministic
   `uuid5` PKs** (imported from `core.crud.crm_sync`) and `DELETE`-ing exactly those
   ids — but only rows **absent before** the test ran (pre-existing rows for a real
   profile are left untouched).
5. **Sweeper** `cleanup.py` (`CLEANUP=1 ./test.sh` or `python cleanup.py`) removes any
   residual `e2e-` segments/campaigns/content-items for the tenant, in case a run was
   killed mid-teardown.

---

## 4. SCRUM-93 — Schema & Migration Foundation

**ACs:** (a) migrations apply & rollback cleanly; (b) new FKs enforce consistency
(lead-source, campaign↔template/segment/content); (c) RLS on all new tenant-scoped
tables; (d) SQLAlchemy models + Pydantic schemas updated.

| ID | AC | Scenario | Type | Method | Expected | Cleanup |
|----|----|----------|------|--------|----------|---------|
| S93-01 | d | Create campaign with new EM columns (`segment_id`, `approval_status`, `strategy_summary`, `ai_plan`) and read back | Positive | E2E | 201; all fields persist + round-trip on GET | delete campaign |
| S93-02 | d | `ai_plan` JSONB round-trips as a nested object | Positive | E2E | GET returns identical dict | delete campaign |
| S93-03 | d | `approval_status` accepts each of Draft/InReview/Approved/Rejected | Boundary | E2E | 201 for each | delete campaigns |
| S93-04 | d | `approval_status = "Bogus"` rejected | Negative | E2E | 422 (Pydantic pattern) | none (not created) |
| S93-05 | b | `crm_campaign.segment_id` FK → non-existent segment | Integrity | E2E | 4xx (FK violation surfaced) or SET NULL semantics documented | delete campaign if created |
| S93-06 | d | `crm_lead.lead_source_id` set on create + read back | Positive | E2E | 201; `lead_source_id` persists | delete lead, lead-source |
| S93-07 | b | `crm_lead.lead_source_id` → non-existent lead-source | Integrity | E2E | 4xx FK violation | delete lead if created |
| S93-08 | c/d | `crm_segment_sync_runs` list endpoint queryable + tenant-scoped | Positive | E2E | 200 list (`[]` for empty tenant) | none |
| S93-09 | c | RLS: a sync-run created under tenant A not visible/editable to tenant B | Isolation | E2E | GET run as other tenant → 404 | segment delete cascades run |
| S93-10 | a | Forward migration `002_*.sql` applies (idempotent, re-runnable) | Positive | DB smoke | tables/columns/FK/index/RLS present; second run no-op | scratch DB dropped |
| S93-11 | a | Rollback `002_*.down.sql` reverses cleanly | Positive | DB smoke | all objects dropped in dependency-safe order | scratch DB dropped |
| S93-12 | c | RLS `ENABLE`+`FORCE`+`tenant_policy` on the 3 new tables | Integrity | DB smoke | `pg_policies` shows `tenant_policy`; blank `app.tenant_id` → 0 rows | scratch DB dropped |
| S93-13 | d | Models importable & mapped (`EmailTemplate`, `CampaignContentItem`, `SegmentSyncRun`) | Positive | Unit | import + metadata assert | n/a |
| S93-14 | b | `crm_campaign_content_items` unique `(campaign_id, content_item_id)` | Integrity | DB smoke / Unit | duplicate link rejected | scratch DB |

> **Not E2E-automatable:** S93-10/11/12/14 have **no HTTP surface** (migrations run via
> `run-sql.sh`; `crm_campaign_content_items` has no router). They are covered by a
> **DB smoke** (`psql` apply-then-rollback on a scratch DB — see §6) and by the unit
> suite. S93-13 is covered by `models/__init__.py` import + existing unit tests.

---

## 5. SCRUM-94 — Segment-ID Driven CRM Sync Engine

**ACs:** (a) one `segment_id` syncs with deterministic per-route counts; (b) routing
rules match exactly (A customer / B lead / C contact); (c) idempotent on replay;
(d) tenant isolation end-to-end. Plus DoD: audit evidence.

### 5.1 Endpoint contract, dry-run, audit, guards (default suite — zero write footprint)

| ID | AC | Scenario | Type | Method | Expected | Cleanup |
|----|----|----------|------|--------|----------|---------|
| S94-01 | — | `/health` reachable (unauth) | Positive | E2E | 200 | none |
| S94-02 | a | `POST /sync-segment/{id}?dry_run=true` | Positive | E2E | 200; `dry_run=true`; `detail.recomputed=false`; `error=0`; count keys present | segment (cascade) |
| S94-03 | a | Real `POST /sync-segment/{id}` on zero-match segment | Positive/Boundary | E2E | 200; `Completed`; all counts 0; `matched=0` | segment (cascade) |
| S94-04 | a | Count invariant holds | Integrity | E2E | `customer+lead+contact+skipped == matched`; `error ≤ matched` | segment |
| S94-05 | DoD | Run audited: retrievable by id + listed for segment | Audit | E2E | GET `/sync-runs/{id}` 200; appears in `/sync-runs?segment_id=` | segment (cascade) |
| S94-06 | DoD | Audit row fields populated (`status`, counts, `started_at`, `dry_run`, `metadata`) | Audit | E2E | fields present & typed | segment |
| S94-07 | c | Replay same segment → identical per-route counts | Idempotency | E2E | counts equal across 2 runs | segment |
| S94-08 | a | Unknown `segment_id` | Negative | E2E | 404 | none |
| S94-09 | a | Segment with no `sql_rules` | Negative | E2E | 400 | delete segment |
| S94-10 | a | Segment with unsafe `sql_rules` (`;`/DML) rejected at create OR sync | Security | E2E | 422 on create (validator) / 400 on sync | delete segment if created |
| S94-11 | d | No auth / no tenant context | Security | E2E | not 200 → 401/403 (SSO) or 400 (no tenant) | none |
| S94-12 | d | Non-admin token (no tenant-admin role) | Security | E2E (opt) | 403 | none |
| S94-13 | d | Cross-tenant: sync a segment owned by another tenant | Isolation | E2E | 404 | none |
| S94-14 | d | Cross-tenant: read another tenant's sync-run | Isolation | E2E | 404 | segment (cascade) |
| S94-15 | — | `/sync-runs` list respects `limit` bounds | Boundary | E2E | `limit=0`/`limit=101` → 422 | none |
| S94-16 | — | `/sync-runs?segment_id=` filter scopes results | Positive | E2E | only that segment's runs | segment |

### 5.2 Routing behaviour (opt-in `E2E_ALLOW_DATA_WRITES=1` — self-cleaning writes)

Targets an existing UAT profile per lifecycle stage via `sql_rules =
master_profile_id = '<uuid>'` (matches exactly one). Skips if no profile of that stage
exists. Deletes only rows it created (pre-existence-checked).

| ID | AC | Scenario | Type | Method | Expected | Cleanup |
|----|----|----------|------|--------|----------|---------|
| R-A1 | b | Route A: `lifecycle_stage='customer'` → `crm_customer_contacts` (if eligible signal) | Positive | E2E-write | `customer=1`; customer-contact row exists at deterministic id | delete customer-contact + segment |
| R-A2 | b | Route A: no transaction facts → **no fabricated** `crm_transactions` | Boundary | E2E-write | `transactions_written=0`; no tx row | delete customer-contact + segment |
| R-A3 | b | Route A: profile with `attributes.transactions` → tx upserted verbatim (amount not fabricated) | Positive | E2E-write | tx row amount == source | delete tx + customer-contact + segment |
| R-B1 | b | Route B: `lifecycle_stage='lead'` → `crm_lead` + `crm_lead_source` from `acquisition_source` | Positive | E2E-write | `lead=1`; lead row w/ `lead_source_id`; lead-source row | delete lead, lead-source, segment |
| R-B2 | b | Route B: lead with no `acquisition_source` → fallback source `segment_sync` | Boundary | E2E-write | lead-source name == `segment_sync` | delete lead, lead-source, segment |
| R-B3 | b | Route B: lead with no identity field → skipped | Boundary | E2E-write | `skipped=1`, `lead=0` | segment |
| R-C1 | b | Route C: other stage (`prospect`/`vip`/…) → `crm_contact` | Positive | E2E-write | `contact=1`; contact row exists | delete contact + segment |
| R-D1 | c | Idempotent writes: replay → same deterministic ids, no duplicates | Idempotency | E2E-write | row count unchanged; same ids | delete created rows + segment |

### 5.3 Routing logic exhaustive coverage (unit — already in repo)

`tests/test_crm_sync_crud.py` covers routing/idempotency/dry-run/error-isolation with a
faked session (no DB), including `classify_route` for every stage, no-fabrication,
skip-without-identity, savepoint rollback accounting, and recompute-failure auditing.
Referenced here so §5.2 opt-in tests stay small and low-footprint.

---

## 6. Cases requiring DB access (not HTTP)

Run once on a scratch Postgres (or a UAT maintenance window) — not part of the
per-run E2E suite:

```bash
# apply-then-rollback smoke (S93-10/11/12)
psql "$DB" -f database-init/migrations/002_email_marketing_schema_foundation.sql   # apply (idempotent)
psql "$DB" -f database-init/migrations/002_email_marketing_schema_foundation.sql   # re-apply: no-op
psql "$DB" -c "\d+ customer360.crm_segment_sync_runs"                              # columns/index present
psql "$DB" -c "SELECT polname FROM pg_policies WHERE tablename='crm_segment_sync_runs';"  # tenant_policy
psql "$DB" -f database-init/migrations/002_email_marketing_schema_foundation.down.sql     # rollback clean
```

## 7. Coverage summary

| AC | Covered by |
|----|-----------|
| S93-a migrations apply/rollback | DB smoke (§6) |
| S93-b FK consistency | E2E S93-05/07 + DB smoke S93-14 |
| S93-c RLS on new tables | E2E S93-08/09 + DB smoke S93-12 |
| S93-d models/schemas updated | E2E S93-01..08 + unit S93-13 |
| S94-a deterministic counts | E2E S94-02..09, S94-16 |
| S94-b routing rules exact | E2E-write R-A/B/C + unit §5.3 |
| S94-c idempotent replay | E2E S94-07 + E2E-write R-D1 + unit |
| S94-d tenant isolation/authz | E2E S94-11..14 + unit |
| DoD audit evidence | E2E S94-05/06 |

**Automated E2E (default, no writes):** S93-01..09, S94-01..16.
**Automated E2E (opt-in writes, self-cleaning):** R-A1..R-D1.
**DB smoke / unit:** S93-10..14, S94 routing logic.

---

## 8. SCRUM-97 — Dagster Execution Modernization (campaign activation + email dispatch)

> **Deploy gate:** these hit endpoints added on `feat/SCRUM-92/subtask-05-06`. They **skip** with "…not deployed on target (SCRUM-97/98)" when the target still runs a pre-05/06 build (the probe: public `GET /track/email/open` must return 200). They run for real once this branch is deployed — e.g. via the CI `e2e` stage after a UAT deploy. Run: `CASES=S97 ./test.sh`.

| ID | AC | Scenario | Type | Method | Expected | Cleanup |
|----|----|----------|------|--------|----------|---------|
| S97-01 | guard | `POST /admin/campaigns/{unknown}/activate` | Negative | E2E | 404 | none |
| S97-02 | authz | Activate without auth | Security | E2E | 401/403 (SSO) or 400 | none |
| S97-03 | approval gate | Activate a **Draft** campaign | Negative | E2E | 409 (not Approved) | delete campaign |
| S97-04 | integrity | Activate **Approved** campaign missing template/segment | Negative | E2E | 409 (needs both) | delete campaign |
| S97-05 | audit | `GET /admin/campaigns/{id}/dispatch-logs` for a fresh campaign | Positive | E2E | 200 `[]` | delete campaign |
| S97-06 | config | `GET /admin/email-provider-config` | Positive | E2E | 200 (config or null) | none |
| S97-07 | DoD | Activate a real Approved campaign (template+segment) → Dagster run | Positive | E2E (opt-in `E2E_CAMPAIGN_ID`) | 200 `run_id` (or 503 if Dagster down) | run is idempotent; ledger dedups |
| S97-U | idempotency/retry/failure | send-pipeline logic (dispatch idempotency, savepoints, suppression, adapters) | Unit | `email_engine/tests/*` | — | n/a |

> The full send (render → dispatch → `cdp_campaign_dispatch_logs`) is orchestrated by Dagster and needs an Approved campaign + **Approved template** + segment + recipients; the template has no public CRUD endpoint, so a full E2E can't be seeded via API — S97-07 is opt-in against an existing campaign, and the dispatch/idempotency/adapter logic is covered exhaustively by the `email_engine` unit suite.

## 9. SCRUM-98 — Tracking, Webhooks & Compliance (public endpoints)

> Tokens are minted for **synthetic random** (tenant, campaign, profile) ids using `E2E_EMAIL_TRACKING_SECRET` (must match the deployment; UAT default), so these exercise the contract + security fixes with **zero data footprint** (a random profile has no `cdp_profile_links` row → events skipped, no suppression written). Run: `CASES=S98 ./test.sh`.

| ID | AC | Scenario | Type | Method | Expected |
|----|----|----------|------|--------|----------|
| S98-01 | tracking | Open pixel returns a 1×1 GIF (valid / missing / bad token) | Positive/Boundary | E2E | 200 `image/gif` always |
| S98-02 | tracking | Click redirects to the signed destination | Positive | E2E | 302 → the URL |
| S98-03 | security | **Open redirect blocked** — forged/absent `k` or non-http scheme | Security | E2E | 302 → `/` |
| S98-04 | security | **Webhook fail-closed** — forged/unsigned callback | Security | E2E | 401 (bad sig) or 503 (disabled); never 200-suppress |
| S98-05 | compliance | Unsubscribe confirms (valid token) / rejects invalid token | Positive/Negative | E2E | 200 HTML / 400 |
| S98-U | dedup/suppression | dedup atomicity, suppression `ON CONFLICT`, ledger-address resolution, signature verify | Unit | `tests/test_email_tracking.py` | — |

**Automated E2E (deploy-gated, no footprint):** S97-01..06, S98-01..05. **Opt-in:** S97-07 (`E2E_CAMPAIGN_ID`). **Unit:** S97-U, S98-U.
