# Code Review — SCRUM-93 & SCRUM-94 (consolidated)

**Passes:** Ponytail (over-engineering + correctness) · `/code-review max` (correctness / security / performance / coverage)
**Date:** 2026-09-11
**Branch:** `feat/SCRUM-92/subtask-01-02`
**Base:** `feat/SCRUM-92-leo-cdp-v-2-0-beta-agentic-outbound-email-marketing-execution-engine`
**Scope:** full branch diff (merge-base `1e3f057` → HEAD) — one feature commit `654bcda` *"SCRUM-93/94: email-marketing schema foundation + segment-ID CRM sync engine"*, 21 files, +2241/−9.
**Tickets:** [SCRUM-93 — SUBTASK-01 Schema & Migration Foundation](https://leocdp.atlassian.net/browse/SCRUM-93) · [SCRUM-94 — SUBTASK-02 Segment-ID Driven CRM Sync Engine](https://leocdp.atlassian.net/browse/SCRUM-94)

---

## Verdict

**Approve — ship-worthy; all findings resolved.** The implementation is well-structured, defended at the trust boundaries, and mapped tightly to both tickets' acceptance criteria. Every finding from both passes is now fixed or resolved-as-documented. Suite green.

```
29 passed   (test_crm_sync_crud.py + test_crm_sync_router.py, SSO_LOGIN=true)
```

> **Fixes applied 2026-09-11:**
> - **Ponytail pass:** F1, P2, P3, P5 fixed in `core/crud/crm_sync.py` (F1 & P2 covered by new tests); P4 kept as intentional foundation per SCRUM-93's DoD; I6 left as a process note.
> - **Max pass:** **C1** fixed (`_require_tenant` now UUID-validates → 400); **PF1** documented as a `ponytail:` ceiling (per-batch commit is the upgrade path); **TC1** fixed (two new list-endpoint tests); **Q1** fixed (retry extracted to the already-sourced `deployments/lib/ghcr.sh`, 6 scripts DRY'd); **TC2** resolved-as-documented (needs a live-PG CI job). C2/PF2/PF3 are info/acknowledged.
>
> The code reviewed below reflects the post-fix state.

---

## 1. Overview — what the change does

- **SCRUM-93 (schema foundation):** adds `crm_email_templates`, `crm_campaign_content_items`, `crm_segment_sync_runs`; extends `crm_campaign` (segment/template FKs, approval gate, `strategy_summary`, `ai_plan`) and `crm_lead` (`lead_source_id`). Delivered in **both** the fresh `database-schema.sql` (new-cluster path) and an idempotent forward migration `002_email_marketing_schema_foundation.sql` (+ a manual `.down.sql`), with FKs, indexes, and tenant RLS on the three new tenant-scoped tables. SQLAlchemy models and Pydantic schemas updated.
- **SCRUM-94 (sync engine):** `POST /api/v1/admin/crm/sync-segment/{segment_id}` (+ `dry_run`) recomputes one segment, resolves its members from `cdp_master_profiles`, and routes each by `lifecycle_stage` — customer → `crm_customer_contacts` + `crm_transactions`; lead → `crm_lead` + `crm_lead_source`; else → `crm_contact`. Idempotent (deterministic `uuid5` PKs + `ON CONFLICT`), per-member savepoint isolation, audited in `crm_segment_sync_runs`, with `GET /admin/crm/sync-runs[/{id}]` for evidence.
- **Out-of-scope bundle (same commit):** a GHCR image-pull retry loop across 6 `deploy-*.sh` scripts, a `run-sql.sh` change (in-scope), and a 341-line Dagster UAT scaling doc.

---

## 2. AC coverage

### SCRUM-93 — Schema & Migration Foundation
| Requirement | Status | Evidence |
|---|---|---|
| `crm_email_templates` (all required columns) | ✅ | migration + fresh schema + model + schema |
| `crm_campaign` extended (segment/template FK, approval gate, `strategy_summary`, `ai_plan`) | ✅ | `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` + guarded FK/CHECK |
| `crm_lead.lead_source_id` FK | ✅ | migration + fresh schema + model |
| `crm_campaign_content_items` relation | ✅ | unique `(campaign_id, content_item_id)` prevents dup links |
| `crm_segment_sync_runs` audit | ✅ | per-route counts + timing + dry-run flag |
| Indexes + RLS on new tenant-scoped tables | ✅ | 3 tables added to the RLS loop; `ENABLE`+`FORCE`+`tenant_policy` |
| Migrations apply **and rollback** cleanly | ✅ | `002_*.sql` idempotent (guards); `002_*.down.sql` reverses in dependency-safe order |
| SQLAlchemy models + Pydantic schemas updated | ✅ | `models/crm.py`, `models/__init__.py`, `schemas/crm.py` |

Bonus: `run-sql.sh` now excludes `*.down.sql` from the automated bootstrap, so the rollback script can never be applied during a forward deploy. In-scope and correct.

### SCRUM-94 — Segment-ID Driven CRM Sync Engine
| Requirement | Status | Evidence |
|---|---|---|
| `POST /api/v1/admin/crm/sync-segment/{segment_id}` + dry-run | ✅ | `crm_sync_api.py`; `dry_run` query param |
| Recompute segment, then resolve members | ✅ | reuses `recompute_segment_membership` (no re-implementation) |
| Route A customer → `crm_customer_contacts` + `crm_transactions` | ✅ | `classify_route` + `_upsert_customer_contact`/`_upsert_transactions` |
| Route B lead → `crm_lead` + `crm_lead_source` | ✅ | `_upsert_lead_source` (fallback source) + `_upsert_lead` |
| Route C contact → `crm_contact` | ✅ | all other stages |
| Don't fabricate transaction amounts | ✅ | `amount = fact.get("amount")` (None stays None); `[]` when no facts |
| Idempotency on replay | ✅ | deterministic `uuid5` PKs + `ON CONFLICT DO UPDATE` |
| Persist metrics in `crm_segment_sync_runs` | ✅ | `_build_run` on both success and catastrophic-failure paths |
| Tenant isolation end-to-end | ✅ | router 404s cross-tenant; explicit `tenant_id` filters; RLS |
| Deterministic per-route counts | ✅ | `route_counts` returned + asserted in tests |

Bonus: keyset-paginated member resolution (`master_profile_id > :last_id`) so a large segment never loads its whole membership into memory.

---

## 3. Correctness

**Verified solid** (tests mock the DB, so these were checked against the real schema/code):
- **No SELECT fan-out.** Member resolution reuses `DOMAIN_ATTRIBUTES_JOIN_SQL`, a `LEFT JOIN LATERAL (… LIMIT 1) ON TRUE` — at most one row per `master_profile_id`, so `matched` and per-route counts are exact. The predicate (`tenant_id`, `status_code = 1`, join, `sql_rules`) is identical to `recompute_segment_membership`, so `matched` and `segment.member_count` stay consistent.
- **All raw INSERTs are column- and constraint-correct** across `crm_customer_contacts`, `crm_lead`, `crm_contact`, `crm_lead_source`, `crm_transactions`. Only `tenant_id` is `NOT NULL` on the targets, so the "never fabricate amounts → pass `None`" rule inserts cleanly.
- **Idempotency holds.** Deterministic `uuid5(namespace, natural-key)` PKs + `ON CONFLICT DO UPDATE`; `crm_transactions` rides the partial unique index `ux_crm_transactions_tenant_source` whose predicate (`WHERE source_transaction_id IS NOT NULL`) exactly matches the `ON CONFLICT` clause, and the engine always supplies a non-null `source_transaction_id`.
- **RLS survives mid-run commits.** `recompute` commits inside the run; the `after_begin` listener in `core/database.py` re-applies `app.tenant_id` on the next transaction, so the fail-closed `tenant_policy` stays satisfied for reads and writes that follow.
- **Failure auditing is complete** (post-fix): recompute + per-member loop both run inside the `try`; a catastrophic failure rolls back partial writes and commits a `Failed` audit row before re-raising.

**Open findings:**

| # | Severity | Finding |
|---|---|---|
| C1 | Low — **✅ FIXED** | `_require_tenant` accepted any truthy `X-Tenant-Id` without UUID validation, so a malformed value 500'd (via `uuid.UUID(...)` in the list endpoint / `_as_uuid` in the audit build) instead of a clean 400. **Resolution:** `_require_tenant` now normalizes/validates via `uuid.UUID(...)` and raises 400 on a malformed header. |
| C2 | Info — acknowledged | Route/skipped counts intentionally overlap `error` (a failed member is counted in both its route bucket and `error`), so `customer + lead + contact + skipped` can exceed `matched` on errors. Documented in `SegmentSyncRouteCounts`; no code change — noted so dashboards don't treat the buckets as a partition. |

---

## 4. Security

- **SQL injection:** `segment.sql_rules` is validated by the reused `validate_sql_where_fragment` (blocks stacking/comments, DML/DDL, SELECT/FROM/JOIN/UNION, admin functions, control chars, unbalanced parens) and interpolated inside a single `(...)` wrap — the exact shape the validator assumes. Schema/`_MEMBER_COLUMNS` interpolation is trusted config, not user input. ✔
- **Tenant isolation:** enforced three ways — explicit `tenant_id` predicates, application-level 404s on cross-tenant `segment_id`/`sync_run_id`, and fail-closed RLS. ✔
- **AuthZ:** all four routes call `_require_tenant` + `_enforce_sync_permissions`; sync/audit endpoints gate on a tenant-admin role set when SSO is on, open only in local dev (SSO off) — mirrors the segmentation admin endpoints. ✔
- **Error exposure:** `error_message` truncated to 1000 chars, stored only in the tenant-scoped audit row; API response message is generic. ✔
- No secrets, no new external calls, no deserialization of untrusted input. ✔

---

## 5. Performance

| # | Severity | Finding |
|---|---|---|
| PF1 | Medium-low — **✅ RESOLVED (ceiling documented)** | **The whole segment syncs in one transaction.** `batch_size` (default 500) bounds only the SELECT *fetch* memory via keyset pagination — nothing commits between batches, so every upsert and every per-member `SAVEPOINT` accumulates in a single transaction until the final `db.commit()`. For a large segment (10⁵–10⁶ members) that is a long-running transaction: large WAL/undo, many row locks held to the end, and a big rollback cost if it fails late. **Resolution:** added a `ponytail:` comment in `sync_segment_to_crm` naming the single-transaction ceiling and the per-batch-commit upgrade path (idempotent PKs let a resumed re-run converge). Not implemented — no large-segment need yet, and per-batch commit changes the all-or-nothing atomicity; deferred until segment sizes warrant it. |
| PF2 | Low | O(N) round-trips — one (contact) or two-plus (customer: contact + one INSERT per transaction fact) statements per member. Fine for admin-triggered sync; batch `execute`/`executemany` is the optimization lever if throughput matters. |
| PF3 | — | Indexing is appropriate: keyset scan rides the `master_profile_id` PK; new tables carry tenant/segment/started_at indexes matching the audit query (`WHERE tenant_id … ORDER BY started_at DESC`). ✔ |

---

## 6. Test coverage

- **Strong:** routing by lifecycle stage, idempotent `ON CONFLICT` shape, dry-run (no writes, no recompute), no-fabricated-amounts, per-member error isolation, tenant scoping, and all router guards (404 missing / 404 cross-tenant / 400 no-rules / 400 no-tenant / 400 unsafe-rules). Plus two regression tests added this session (recompute-failure auditing; detail-count not inflated on savepoint rollback). 27 tests, hermetic (fakes, no real PG/Redis).
- **Gaps:**
  - **TC1 (low) — ✅ FIXED:** added `test_list_sync_runs_returns_rows_for_own_tenant` and `test_list_sync_runs_rejects_out_of_range_limit` to `test_crm_sync_router.py` (suite now **29 passed**).
  - **TC2 (info) — 📝 resolved-as-documented:** apply-then-rollback parity needs a live PG, so it's a CI concern, not a unit test. Recommendation stands — a scratch-DB job applying `002_*.sql` then `002_*.down.sql`. Not added here; an untested standalone script no CI runs would be dead scaffolding.

---

## 7. Conventions & code quality

- Idiomatic and consistent: custom router (not `build_crud_router` — sync isn't CRUD, correct), `metadata_ → "metadata"` mapping, `mapped_column` typing, thorough docstrings, explicit route constants for auditability. ✔
- Migration guards (`IF NOT EXISTS`, `pg_constraint` existence checks, RLS `ENABLE`+`FORCE`+`tenant_policy`) match `001_harden_tenant_rls_policies.sql` conventions. ✔
- **Q1 (low, maintainability) — ✅ FIXED:** extracted the retry into a `docker_pull_retry` function in the already-sourced `deployments/lib/ghcr.sh`; all 6 `deploy-*.sh` scripts now call `docker_pull_retry "$IMAGE"` (verified with `bash -n`). Tuning attempts/backoff is now one edit. (These + the Dagster doc remain unrelated to SCRUM-93/94 — see I6.)

---

## 8. Ponytail findings & resolutions

Severity: **F**inding (fix before merge) · **P**olish · **I**nfo/process.

### F1 — Recompute failures are not audited *(low–medium)* — ✅ FIXED
`core/crud/crm_sync.py`
Originally `recompute_segment_membership` ran **before** the `try/except` that writes the `Failed` audit row, so a recompute error gave a raw 500 with **no `crm_segment_sync_runs` row** — contradicting the "run is never silent" guarantee and SCRUM-94's audit DoD.
**Resolution:** recompute moved inside the `try`; a failure there now commits a `Failed` audit row before re-raising. Locked in by `test_recompute_failure_is_audited_as_failed_run`.

### P2 — `detail` write-counts can over-report on savepoint rollback *(low)* — ✅ FIXED
`core/crud/crm_sync.py`
The customer route incremented `detail["customer_contacts_written"]` from `_upsert_customer_contact`'s return **before** `_upsert_transactions` ran in the same savepoint; if the transactions upsert raised, the savepoint rolled back the contact insert too but the counter wasn't decremented, so `metadata.detail` claimed a write that never persisted.
**Resolution:** per-member write tallies stay in locals and fold into `detail` only after the savepoint exits cleanly. Locked in by `test_detail_write_counts_exclude_rolled_back_member`.

### P3 — Transaction idempotency degrades for facts without a `source_transaction_id` *(low)* — ✅ FIXED
`core/crud/crm_sync.py`
The fallback natural key is positional — `f"{master_profile_id}:{index}"` — so re-sync of id-less facts that reorder between runs creates new rows instead of updating.
**Resolution:** added a `ponytail:` ceiling comment naming the assumption (idempotent only while id-less facts keep their order; facts carrying a real `source_transaction_id` are always stable).

### P4 — Unused schema/model surface (YAGNI, but ticket-sanctioned) *(low)* — ✅ RESOLVED (kept, intentional)
`core/schemas/crm.py`, `core/models/crm.py` (`EmailTemplate`, `CampaignContentItem`)
The `Create`/`Update`/`Read` triads + models for `EmailTemplate` and `CampaignContentItem` are defined but not registered on any router or referenced this branch; only `SegmentSyncRun` is fully wired.
**Resolution:** kept as deliberate foundation (decision confirmed 2026-09-11). SCRUM-93's DoD requires "Pydantic schemas updated", the triad matches every other entity in the file, and SUBTASK-03+ will consume them. No code change.

### P5 — No guard against concurrent syncs of the same segment *(low)* — ✅ FIXED (ceiling noted)
`core/crud/crm_sync.py`
Two overlapping runs of one `segment_id` both recompute and upsert; deterministic PKs + `ON CONFLICT` make them converge but they can race on the recompute write or `ON CONFLICT`.
**Resolution:** added a `ponytail:` comment naming the ceiling and upgrade path (a `pg_advisory_xact_lock` on `(tenant_id, segment_id)`). No lock added — acceptable, documented ceiling for an admin-triggered action.

### I6 — Scope creep in the commit *(process)* — ⚠️ NOT FIXED (process note)
Commit `654bcda` bundles changes unrelated to SCRUM-93/94, which its own message admits ("Also included: GHCR image-pull retry … and the Dagster scale-out UAT guide"):
- `deployments/server/deploy-*.sh` ×5 + `deploy-ads.sh` + `deploy-frontend.sh` — identical GHCR pull-retry loop (logic is fine: bounded 5 attempts, linear backoff, non-zero exit on exhaustion).
- `deployments/docs/dagster-scaling-uat-vserver.md` — 341-line ops guide.

**Resolution:** left as-is. Rewriting `654bcda` to split these out would be destructive history rewriting on a shared branch; the recommendation stands for the *next* commit/PR split. (`run-sql.sh` is the exception — it *is* in-scope.)

### N7 — Nits *(no action needed)*
- `CRM_SYNC_NAMESPACE`: the comment already concedes a literal UUID would do the same; uuid5-of-a-name is fine and self-documenting.
- Recompute is arguably redundant to member resolution: `_iter_segment_members` re-runs `sql_rules` against `cdp_master_profiles` directly rather than reading the recomputed membership. Kept because the AC mandates "recompute first, then resolve" (and, per §3, both paths use the same predicate so counts stay consistent).

---

## 9. Prioritized suggestions (open items)

All items resolved 2026-09-11:

1. **PF1** — ✅ ceiling documented in `crm_sync.py` (per-batch commit is the tracked upgrade path; deferred, no large-segment need yet).
2. **TC1** — ✅ two list-endpoint tests added.
3. **C1** — ✅ `_require_tenant` UUID-validates (400 on malformed input).
4. **Q1** — ✅ retry extracted to `deployments/lib/ghcr.sh`; 6 scripts DRY'd.
5. **TC2** — 📝 resolved-as-documented (live-PG CI job; not unit-testable here).

Remaining info-only (no code change): **C2** (documented count overlap), **PF2** (O(N) round-trips — optimization lever), **PF3** (indexing already correct). Both tickets' ACs are met; suite green at **29 passed**.

---

## 10. Risk summary

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Long transaction on a very large segment (PF1) | Medium (scales with segment size) | Lock contention / slow commit / late-rollback cost | ✅ Ceiling documented; per-batch commit is the tracked upgrade path when large segments appear |
| Malformed tenant header → 500 (C1) | Resolved | — | ✅ `_require_tenant` UUID-validates → 400 |
| Retry block drift across 6 scripts (Q1) | Resolved | — | ✅ Extracted to `deployments/lib/ghcr.sh` |
| Untested list endpoint (TC1) | Resolved | — | ✅ Two tests added |
| Migration rollback not CI-smoked (TC2) | Low | Rollback regressions slip through | 📝 Add a scratch-DB up/down CI job (documented) |

---

## 11. Over-engineering scorecard (ponytail)

The engine is lean for what it does — explicit route constants, small single-purpose helpers, stdlib-only (`uuid5`, `json`), reused segmentation + SQL-safety modules rather than re-implementing them.

```
net: 0 lines cut — P4 kept intentionally (ticket DoD + file consistency).
     F1/P2/P3/P5 resolved via a correctness fix + ceiling comments, not
     deletions. The two added regression tests are the ponytail minimum.
```

**Overall: approve — all findings resolved.** Correct, well-tested, tenant-safe, and faithful to both tickets. C1/TC1/Q1 are fixed in code; PF1 and TC2 are documented as tracked ceilings (PF1 = per-batch commit when large segments arrive; TC2 = a scratch-DB migration up/down CI job). Suite green at **29 passed**.

---

## 12. Parent-epic alignment — [SCRUM-92](https://leocdp.atlassian.net/browse/SCRUM-92) (added 2026-09-11)

Reviewed the branch against the **parent story** SCRUM-92 *"Agentic Outbound Email Marketing Execution Engine"* through the ponytail (over-engineering) and code-review (correctness/NFR) lenses. SCRUM-92 is an **8-subtask epic**; this branch implements **only SUBTASK-01 (SCRUM-93) + SUBTASK-02 (SCRUM-94)**. The other six are `To Do` and out of this branch's scope.

### Subtask scope map
| Subtask | Status | This branch |
|---|---|---|
| 01 Schema & Migration Foundation | In Progress | ✅ implemented |
| 02 Segment-ID CRM Sync Engine | In Progress | ✅ implemented |
| 03 AI Email Template Authoring (Gemini/OpenAI) | To Do | ⛔ out of scope |
| 04 AI Campaign Strategy & Draft | To Do | ⛔ out of scope |
| 05 Dagster dispatch modernization | To Do | ⛔ out of scope |
| 06 Tracking / Webhooks / Compliance | To Do | ⛔ out of scope |
| 07 C360 feedback & performance rollups | To Do | ⛔ out of scope |
| 08 E2E automated test suite | To Do | 🟡 seeded — `tests/e2e` covers 01/02 |

### Epic Gate checklist (A–D), for the parts 01/02 own
| Item | Status | Note |
|---|---|---|
| **A. Naming** — handler `verb_noun_scope` | ✅ | epic's own example is `sync_segment_crm`; our router fn is literally `sync_segment_crm`. Tables `crm_*`/`cdp_*`/`sys_*` ✓ |
| A. env var naming | 🟡 nit (N1) | epic suggests `CRM_EMAIL_*`/provider-standard; ours is `CRM_SYNC_BATCH_SIZE` (sync-domain, defensible; no change) |
| **B. Schema** — templates/campaign/lead FK/content-items/sync-runs + FK+unique+RLS + fwd/rollback | ✅ | all delivered |
| B. `cdp_campaign_dispatch_logs` idempotency keys | ⏭️ N2 | dispatch concern → SUBTASK-06 (correctly untouched) |
| **C. Backend** — `POST …/sync-segment/{id}` + dry-run, recompute-before-sync, exact routing, idempotent upserts | ✅ | rest of C (AI/dispatch/tracking/feedback) = 03–07 |
| **D. Env config** — `CRM_EMAIL_*`/AI/SMTP/webhook | ⏭️ | belong to 03–06; correctly **not** added (YAGNI) |

### Ponytail lens
- **P4 vindicated:** the unused `EmailTemplate` / `CampaignContentItem` Create/Update schemas are epic-mandated foundation consumed by SUBTASK-03/04 — not speculation.
- **`backend-system/data_synch` Dagster job deliberately not built** (N3): the synchronous API engine satisfies SCRUM-94's AC; the async/Dagster path is the documented PF1 ceiling, to add only when scale demands. Building it now would be duplicate work.
- **Zero scope creep into 03–08** — no AI/SMTP/webhook/dispatch code. `Lean. Ship.`

### Code-review lens (SCRUM-92 NFR beta gates)
Tenant isolation & RLS ✅ · SQL safety for generated filters ✅ · audit logging for **sync** ✅ (`crm_segment_sync_runs`) · secrets management ✅ (E2E reuses existing repo secrets). Deliverability circuit breaker + dispatch/webhook audit = SUBTASK-05/06 (out of scope). Correctness already hardened (§8/§9); 29 unit + 21 E2E green on UAT + CI green.

### Verdict
**SUBTASK-01 & 02 correctly and completely implement SCRUM-92's foundation + sync requirements** — exact naming, exact routing, idempotent/tenant-safe/audited, migrations that roll back, and no over-building into the six later subtasks. Non-actionable notes for future subtasks: **N1** (`CRM_SYNC_*` vs `CRM_EMAIL_*`), **N2** (`cdp_campaign_dispatch_logs` idempotency → 06), **N3** (`data_synch` Dagster job → optional async path).
