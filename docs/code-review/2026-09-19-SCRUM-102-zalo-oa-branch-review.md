# Code Review — SCRUM-102 Zalo OA / ZNS branch vs `main`

- **Branch:** `feat/SCRUM-102-leo-cdp-v-2-0-beta-agentic-outbound-zalo-oa-marketing-execution-engine`
- **Base:** `main` (merge-base `143e5b1`)
- **Date:** 2026-09-19
- **Scope:** `git diff main...HEAD` — 177 files, +4253 / −1040. New `notification_engine` service, `leo_customer360_dao` package extraction, ZNS AI planner, data-tracking channel webhooks.
- **Lenses:** correctness/security (`/code-review max`) + over-engineering (ponytail).

**Verification legend:** ✅ verified directly against the code during review · ◦ code read / reviewer-verified, not independently re-run.

## Revision history

- **r1 (2026-09-19)** — Initial review: 14 findings across correctness/security (`/code-review max`) and over-engineering (ponytail).
- **r2 (2026-09-19)** — #1 **suppressed** (team decision). #5 **reframed**: the real root cause is `run-sql.sh` applying `database-schema.sql` before `migrations/` with no ledger, so "restore migration 003" wouldn't work — the rename must live in the schema itself.
- **r3 (2026-09-19)** — All active findings fixed and committed; resolution table + per-finding ✅ FIXED markers added.
- **r4 (2026-09-19)** — #13 **reclassified** after reviewer feedback: `dispatch()`, `all_zalo_tracking_routers`, and base `provider_name` are interface/convention members whose siblings are kept even when uncalled → all **restored**. Net from #13: nothing removed.
- **r5 (2026-09-19)** — `origin/main` merged again, bringing the **connector-config refactor** (`crm_email_provider_config` → `crm_connector_config`). Adaptation verified — see *Post-review: main merges* below. No code changes required; CI green on the merged commit (`28256b8`).

---

## Summary

| # | Severity | Finding | File |
|---|----------|---------|------|
| 2 | 🔴 High | Opt-out webhook returns 200 on ingest failure → consent lost | `channel_webhook.py` |
| 3 | 🔴 High | Unconnected tenant → whole segment falsely ledgered `Sent` (silent mock) | `adapters.py` / `send.py` |
| 4 | 🔴 High | Double-send on paid channel (send before ledger, savepoint rollback) | `send.py` |
| 5 | 🟠 Med | Rename has no upgrade path — runner ordering (schema before migrations, no ledger) strands beta-cycle template data | `database-init/` + `run-sql.sh` |
| 6 | 🟠 Med | Token refresh strands rotated refresh_token on any post-refresh error | `token_refresh.py` |
| 7 | 🟠 Med | `zalo_zns` campaign without `template_data` sends empty params | `send.py` |
| 8 | 🟠 Med | O(all-objects) S3 rescan every 15 min (StartAfter/Prefix mismatch) | `s3_reader.py` |
| 9 | 🟠 Med | RLS-bypass-dependent SELECTs fail silently if role lacks BYPASSRLS | `token_refresh.py` / `optout_projection.py` |
| 10 | 🟡 Low | "Framework-neutral" DAO imports `fastapi`, raises HTTP, undeclared dep | `crud/zalo_oa.py` |
| 11 | 🟡 Low | Webhook factory duplicates email HMAC/token code (email never ported) | `channel_webhook.py` |
| 12 | 🟡 Low | `core/cache.py` `get_redis_client` duplicates DAO copy byte-for-byte | `core/cache.py` |
| 13 | 🟡 Low | "Dead code" — on review, all three items are interface/convention members; none removed | multiple |
| 14 | 🟡 Low | CI backend runner skips new `notification_engine` / `campaign_activation` tests | `.github/workflows/ci.yml` |

## Resolution status (2026-09-19)

All active findings fixed on branch `feat/SCRUM-102-...`; **#1 suppressed** (team decision, see end).

> Note: these fixes were later **squashed into the single branch commit**; the SHAs below are the original logical grouping, not separate commits on the branch now.

| # | Status | Commit |
|---|--------|--------|
| 2 | Fixed — 503 retry when a suppression event fails to persist | `88aaf18` |
| 3 | Fixed — refuse to send (not silent mock) when `zns` configured but OA unconnected | `fcdd8e3` |
| 4 | Fixed — intent-first, one commit per recipient; `Sending` not re-sent (at-most-once) | `fcdd8e3` |
| 5 | Fixed — idempotent rename folded into `database-schema.sql` before the CREATE | `0d816e1` |
| 6 | Fixed — defensive `expires_in` coercion so refresh can't strand the rotated token | `fcdd8e3` |
| 7 | Fixed — reject a `zalo_zns` campaign missing template-declared params | `fcdd8e3` |
| 8 | Fixed — prefix-scoped, window-bounded S3 listing | `9747b0c` |
| 9 | Fixed — BYPASSRLS reliance documented at both driver SELECTs | `0d816e1` |
| 10 | Fixed — DAO raises domain `ZaloOAError`; no `fastapi` import | `88aaf18` |
| 11 | Fixed (partial) — HMAC verify shared via `core/webhook_security.py`; full email→factory port deliberately skipped (GET routes don't fit) | `609dfc7` |
| 12 | Fixed — `core/cache.py` imports the DAO's `get_redis_client` | `0d816e1` |
| 13 | Reviewed — all 3 items (`dispatch()`, `all_zalo_tracking_routers`, base `provider_name`) are interface/convention members whose siblings are kept even when uncalled; **none removed** | squashed |
| 14 | Fixed — CI runs all backend suites; new `notification_engine/run_tests.sh` | `0d816e1` |

**Follow-ups noted, not blocking:** dedicated tests for the new send/ledger logic and the #3/#7 guards; a provider-side idempotency key would let #4 move from at-most-once to safe-retry; consider a migration ledger so `run-sql.sh` stops re-running every file.

**Deferred (not counted):** the `⚠️`-marked Zalo API contract guesses (endpoints, `secret_key` header, webhook signature scheme) are flagged in-code as unverified and must be corrected before real callbacks work.

## Post-review: main merges & adaptation

**Merge of `origin/main` — connector-config refactor (`290bf9e`).** Main replaced `crm_email_provider_config` with a generic `crm_connector_config` (multi-channel EMAIL/SMS/PUSH), refactored the DAO models/CRUD/schemas, enhanced the suppression list for omnichannel, and re-added migrations `002_crm_connector_config.sql` / `003_crm_suppression_list.sql`.

**Impact on this branch: none — no code changes required.** The refactor is backward-compatible and the merge integrated coherently:

- Model alias kept: `EmailProviderConfig = ConnectorConfig`; CRUD names unchanged (`get_active_config` / `upsert_config`) — our imports still resolve.
- The only `crm_email_provider_config` references are inside main's own migration `002` (the data-transition script), not our code.
- `database-schema.sql` holds **both** the folded `crm_message_templates` rename (finding #5) **and** main's new `crm_connector_config` table — no conflict, no leftover conflict markers.
- The co-modified `campaign_activation_api.py` adopted main's connector model (`ConnectorConfig`, generic `config`/`credentials` JSONB); our branch had no Zalo logic there (ZNS routing lives backend-side in `campaign_activation/activation.py`), so nothing was lost.

**Verification.** All co-modified files compile; local suites pass — `campaign_activation` 12, `notification_engine` 7, `email_engine` 18, and `campaign_activation_router` + `dagster_client` + `zalo_planner` 30. CI on the merged commit (`28256b8`): 26 checks pass, 1 skip (`deploy`).

> Note: the merge means the branch is no longer a single commit (a merge commit sits on top of the squashed fix commit). This is the normal result of merging `main`; can be re-squashed on request.

---

## 🔴 High — security & data integrity

> **Finding 1 (Cross-tenant Zalo OA hijack) — SUPPRESSED** at the team's direction on 2026-09-19. See the *Suppressed findings* section at the end for the rationale and the residual risk that remains on record.

### 2. ◦ Opt-out webhook loses consent silently

> ✅ **FIXED** — commit `88aaf18`
**`data-tracking-api/core/routers/channel_webhook.py`** — `record_channel_event` wraps everything in `try/except` that only logs, and the handler still returns `{"status":"ok"}` (200).

**Failure scenario:** Zalo posts `user_unfollow`; S3/tracking ingest throws (outage/cred issue). The except logs a warning; handler returns 200 so Zalo won't redeliver. Because the design is S3-first (no DB write in the handler), that opt-out is permanently dropped and the profile keeps `zalo_opt_in=true` → the user is messaged after unsubscribing (consent/compliance breach).

**Fix:** return a non-2xx status when ingest of a *suppression* event fails so the provider retries.

### 3. ✅ Unconnected tenant → whole segment falsely `Sent`

> ✅ **FIXED** — commit `fcdd8e3`
**`backend-system/notification_engine/notification_engine/adapters.py:80`** (`build_zns_adapter`), **`send.py:226-228`**.

`build_zns_adapter` returns `MockZNSAdapter` whenever `access_token` is falsy — even with the connector's dispatch adapter set to `zns` — and `send_zalo_campaign` has no "OA connected" precondition.

**Failure scenario:** prod `adapter=zns`, tenant never completed OAuth → `load_oa_token` returns `{'access_token': None}` → `if provider == 'zns' and access_token:` is False → `MockZNSAdapter()`. Every recipient gets `status='Sent'`, `provider='zalo_zns_mock'`, zero real delivery, and those terminal `Sent` rows permanently block re-send after the OA is later connected. Unlike `email_engine` (selects SMTP by provider name, fails loudly), ZNS silently reports success.

**Fix:** raise `NotificationEngineError` when `adapter is None and not token`.

### 4. ✅ Double-send on a paid channel

> ✅ **FIXED** — commit `fcdd8e3`
**`backend-system/notification_engine/notification_engine/send.py:~300` (`_process_batch`).**

`adapter.send()` (external, non-transactional) runs *before* `_upsert_dispatch`, both inside `SAVEPOINT recipient`.

**Failure scenario:** `adapter.send()` delivers successfully; `_upsert_dispatch` then errors (transient DB blip) → the except does `ROLLBACK TO SAVEPOINT recipient`, erasing the just-written `Sent`. Next run `_current_status` returns `None` → the recipient is sent a duplicate ZNS (and re-billed).

**Fix:** record intent before send, or reconcile via `provider_message_id` on replay.

---

## 🟠 Medium — ops / upgrade

### 5. ✅ Rename has no working upgrade path (deeper than "003 was deleted")

> ✅ **FIXED** — commit `0d816e1`
**`database-init/database-schema.sql:2676`**, **`deployments/postgres/run-sql.sh`**, deleted **`database-init/migrations/003_crm_message_templates.sql`**.

The first-pass finding ("migrations 002/003 were deleted; restore the rename migration") is only half right. Investigating the actual deploy mechanics **corrects the root cause and the fix**:

**What was deleted, and by whom.** `003_crm_message_templates.sql` *was* a well-formed idempotent rename (guarded `ALTER TABLE crm_email_templates RENAME TO crm_message_templates`, `ADD COLUMN IF NOT EXISTS` for `message_type`/`persona_id`/`context`/`message_body`, backfill `message_body := COALESCE(text_body, html_body)`, constraints, indexes). It was deleted on **`main`** (commit `c43aefb`), not on the Zalo branch — the branch merely inherits it via the merge.

**Why restoring 003 would NOT fix it.** The runner `run-sql.sh` has **no applied-migration ledger** — every deploy re-runs *every* file, in a fixed order: `postgres/**` → `database-schema.sql` → `init-core-database.sql` → `data-view-for-llm.sql` → `migrations/*.sql`. So `database-schema.sql` runs **before** `migrations/`. On a pre-rename DB:

1. `database-schema.sql`'s `CREATE TABLE IF NOT EXISTS crm_message_templates` runs first → creates a fresh **empty** table.
2. Even if `003` were present, its guard `... AND to_regclass('crm_message_templates') IS NULL` is now **false** → the rename **silently skips**.

Net: through this runner the schema-CREATE always wins, so the "new table in schema + rename in a later migration" split is structurally broken for an in-place rename — independent of whether 003 exists.

**Real blast radius (downgraded to Medium).** `crm_email_templates` is a **brand-new v2.0-beta table** (introduced `654bcda` SCRUM-93/94, `4561391` SCRUM-92 — the same beta cycle as the rename). It is not a long-lived prod table. Data loss requires a DB that (a) populated email templates during SCRUM-92/93/94, (b) never got renamed while `003` was live, and (c) is redeployed on this branch. Fresh installs are unaffected; already-renamed DBs are unaffected (`CREATE IF NOT EXISTS` is a no-op, data intact). Realistic exposure is a dev/uat env with beta email-template rows, not prod.

**Correct fix (order-safe, ledger-free).** Fold a guarded rename **into `database-schema.sql`, positioned immediately *before* the `CREATE TABLE IF NOT EXISTS crm_message_templates`**, so the file that runs first does the rename, and the following CREATE becomes a no-op:

```sql
DO $$
BEGIN
    IF to_regclass('customer360.crm_email_templates') IS NOT NULL
       AND to_regclass('customer360.crm_message_templates') IS NULL THEN
        ALTER TABLE customer360.crm_email_templates RENAME TO crm_message_templates;
    END IF;
END $$;
-- ... existing CREATE TABLE IF NOT EXISTS crm_message_templates (...) stays below,
--     plus ADD COLUMN IF NOT EXISTS message_type/persona_id/context/message_body
--     for a table that was renamed from the old shape.
```

Restoring `003` on its own does **not** work here because of the ordering; the rename must live in (or ahead of) the schema file itself.

**Systemic note.** Because `run-sql.sh` re-runs everything with no ledger, *every* migration must be fully idempotent **and** order-safe relative to `database-schema.sql`. Any migration that assumes "schema hasn't created X yet" is unsafe under this runner. Worth a follow-up: either add a migration-ledger (only-once, in-order) or keep the strict "everything idempotent + schema-first" contract documented.

### 6. ◦ Token refresh strands the rotated refresh_token

> ✅ **FIXED** — commit `fcdd8e3`
**`backend-system/notification_engine/notification_engine/token_refresh.py:70`.**

On a successful refresh (Zalo has already rotated + invalidated the old refresh_token server-side), any exception before commit rolls back the DB write. `int(data.get('expires_in', 3600))` raises `TypeError` when Zalo returns `expires_in: null` (key present → `None`), or the UPDATE fails → `rollback()` discards `new` (with the rotated token). DB keeps the dead old refresh_token → every future refresh 400s → access_token expires → sends fall through to mock (compounds #3).

**Fix:** coerce `expires_in` defensively; persist the rotated token durably before treating refresh as done.

### 7. ◦ Campaign without `template_data` sends empty params

> ✅ **FIXED** — commit `fcdd8e3`
**`send.py:168` (`_base_template_data`).**

Returns `{}` when a `zalo_zns` campaign has neither `ai_plan.template_data` nor `metadata.template_data`. The generic Campaign CRUD (`build_crud_router`) lets a marketer create a `channel='zalo_zns'` campaign with a `template_id` directly (no `ai_plan`) and get it Approved → `render_params` yields `{}` → `adapter.send` posts empty `template_data` → Zalo rejects every recipient → 100% Failed with no clear operator signal.

**Fix:** validate non-empty `template_data` (covering every template-required param) before dispatch, or reject such campaigns at approval.

### 8. ◦ O(all-objects) S3 rescan every run

> ✅ **FIXED** — commit `9747b0c`
**`s3_reader.py:117`.**

`StartAfter` is a `"YYYY-MM-DD-HH"` string that doesn't align with the Bronze key layout, and no `Prefix` is set, so each 15-min run re-lists / re-downloads / re-parses every object in each tenant bucket. Idempotency preserves correctness; cost grows without bound until the op times out and opt-out projection stalls. (If keys *do* sort by a different date format, recent opt-outs get skipped instead.)

**Fix:** partition-prefixed listing keyed to the actual Bronze key layout.

### 9. ◦ RLS-bypass-dependent driver SELECTs fail silently

> ✅ **FIXED** — commit `0d816e1`
**`token_refresh.py:43`, `optout_projection.py:74` (`read_optout_events`).**

Both driving `SELECT`s query `sys_data_source` without setting tenant context, relying on the backend DB role holding `BYPASSRLS`. If the role is not `BYPASSRLS` (or RLS is enforced for it), `current_setting('app.tenant_id')` is unset → RLS fails closed → 0 rows. Result: no tokens ever refreshed, no opt-out events ever projected — silently. Matches the `identity_resolution` pattern but is a silent failure mode.

**Fix:** assert/log the `BYPASSRLS` reliance so a misconfigured role is loud, not silent.

---

## 🟡 Low — cleanup (correctness + over-engineering)

### 10. ◦ DAO imports `fastapi` and raises HTTP

> ✅ **FIXED** — commit `88aaf18`
**`customer360-dao/src/leo_customer360_dao/crud/zalo_oa.py:21`.**

The "framework-neutral" DAO (per `database.py` docstring) imports `fastapi.HTTPException` and raises 400/502 from data-access code; `fastapi` is not in `customer360-dao/pyproject.toml`. Once `publish-customer360-dao.yml` ships it to PyPI, any non-FastAPI consumer doing `from leo_customer360_dao.crud import zalo_oa` gets `ModuleNotFoundError: fastapi`. Also inverts layering (DAO deciding HTTP status).

**Fix:** return domain errors / `None` and let the router map to HTTP (or at minimum declare `fastapi`).

### 11. ◦ Webhook factory duplicates email security code

> ✅ **FIXED** — commit `609dfc7` (partial — HMAC verify shared; full email→factory port intentionally skipped)
**`data-tracking-api/core/routers/channel_webhook.py` vs `core/routers/email_tracking.py`.**

`_verify_signature` (ch:40) is byte-identical to `verify_webhook_signature` (email:107); `_source_id`/`record_channel_event` duplicate email:116/124. The factory docstring claims it serves "email, Zalo, …" but email was never ported — `email_tracking.py:212` still calls its own copy. Two copies of HMAC-signature/token/S3-envelope logic now drift independently on auth-relevant code.

**Fix:** route `email_tracking` through the same factory (gives the factory a real 2nd caller), or — if email won't migrate soon — inline the factory into `zalo_tracking.py`.

### 12. ✅ `core/cache.py` duplicates the DAO Redis client

> ✅ **FIXED** — commit `0d816e1`
**`customer360-api/core/cache.py:64-85`.**

`get_redis_client` is byte-for-byte identical (bar the docstring) to `leo_customer360_dao.cache.get_redis_client`, and the API already depends on the DAO. Two module-level `_client` singletons = two Redis pools in one process.

**Fix:** `from leo_customer360_dao.cache import get_redis_client`; drop the local copy + its `_client`/`_client_initialized` globals. ~-25 lines.

### 13. ◦ "Dead code" — reviewed, nothing removed

> ↩️ **REVIEWED — nothing removed.** All three were initially cut, then restored: each is an interface/convention member whose siblings are deliberately kept even though uncalled today. Deleting the odd one broke symmetry.
- `data-tracking-api/core/routers/zalo_tracking.py` — `all_zalo_tracking_routers` export: initially removed as unused, then **restored**. The sibling `email_tracking.py` keeps `all_email_tracking_routers` (also unused by `app.py`, which imports `router` directly) — it's a per-module export convention, so removing only zalo's broke symmetry. **Kept.**
- `backend-system/notification_engine/notification_engine/adapters.py` — `DispatchAdapter.provider_name = "base"`: initially removed as never-read, then **restored**. It declares the interface attribute every adapter carries (`send.py` reads `adapter.provider_name`); the base default documents the contract. **Kept.**
- `customer360-api/core/utils/dagster_client.py` — `NotificationEngineDagsterService.dispatch()` was initially removed as "no caller", then **restored**. It is the API-side trigger contract for `notification_engine_job`, symmetric with `EmailEngineDagsterService.send_campaign()` (also uncalled from the API today but deliberately kept). The live activation path submits the same job from `campaign_activation/triggers.py`; the API-side method is the intended entry point if an endpoint wires it up. Not dead code — keeping it preserves the facade convention. **Kept.**

### 14. ◦ CI skips the new suites

> ✅ **FIXED** — commit `0d816e1`
**`.github/workflows/ci.yml:104`.**

The backend-system runner only executes `identity_resolution` + `segmentation` `run_tests.sh`, so the new `notification_engine` (`test_notification_engine.py`) and `campaign_activation` tests never run in CI → a regression in the entire new ZNS engine ships green.

**Fix:** add `notification_engine` / `campaign_activation` `run_tests.sh` to the backend-system runner list.

---

## What the branch does well (not flagged)

- `notification_engine` deliberately mirrors `email_engine` (advisory lock, keyset pagination, per-recipient SAVEPOINT isolation, idempotent dispatch ledger).
- `urllib` used instead of adding a `requests` dependency.
- ZNS AI planner reuses the existing provider abstraction; on-list-template + required-param guardrails are correct.
- `core/database.py` correctly became a thin FastAPI adapter over the DAO session factory.
- `notification_engine/rls.py` + `tracking.py` per-service copies are a *documented*, justified microservice-isolation choice (own `requirements.txt`, no sibling imports) — same pattern `email_engine`/`segmentation` already ship. Not a defect.

---

## Recommended fix order

`#5` (blocks upgrade for beta-cycle DBs) → `#3` + `#6` (silent-mock cluster) → `#2` (compliance) → `#4` (double-send) → remainder.

Low-risk / high-value first batch: **#5, #12.** `#3` / `#4` / `#6` need a small design decision on send/ledger ordering — confirm before implementing.

---

## Suppressed findings

### 1. Cross-tenant Zalo OA hijack — *suppressed 2026-09-19*
The OAuth `state` on the public `/api/v1/auth/zalo-redirect` callback is signed with `dev_jwt_secret` (default `"dev-insecure-secret-change-me-please-32b"`, documented as unused under SSO), enabling a forged-state cross-tenant OA binding.

**Suppressed at the team's direction.** Recorded here for traceability, not being tracked for a fix in this review. **Residual risk if it stays unaddressed:** any env that leaves `DEV_JWT_SECRET` at its default while running SSO exposes the tenant-binding of OA connections to forgery. If suppression is because dev-login is disabled *and* `DEV_JWT_SECRET` is always set to a strong value in every deployed env, the risk is mitigated in practice — confirm that assumption holds before treating it as closed. The reusable lesson (never sign a security token with a secret documented as inert) is retained in the knowledge vault regardless.
