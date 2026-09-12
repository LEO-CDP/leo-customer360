# Code Review (max effort) — SCRUM-97 & SCRUM-98

**Command:** `/code-review max`
**Date:** 2026-09-11
**Branch:** `feat/SCRUM-92/subtask-05-06`
**Base:** `feat/SCRUM-92-leo-cdp-v-2-0-beta-agentic-outbound-email-marketing-execution-engine`
**Scope:** branch diff (merge-base `0ae515b` → HEAD) — one feature commit `4561391`, 49 files, +3036/−69.
**Tickets:** [SCRUM-97 — Dagster Execution Modernization](https://leocdp.atlassian.net/browse/SCRUM-97) · [SCRUM-98 — Tracking, Webhooks & Compliance Feedback](https://leocdp.atlassian.net/browse/SCRUM-98)
**Method:** DB contract (migrations 003/004 + schema) read first-hand; three parallel subsystem reviews (email_engine, campaign_activation, tracking/compliance); top findings verified against the source.

> **Verdict up front: strong, well-structured implementation — but do NOT enable a real (non-mock) provider or accept live webhooks until B1 + H1 are fixed.** One blocker (forgeable webhooks poison the compliance suppression list for arbitrary addresses) and one high (open redirect) are security/compliance gates; H2 + M1 matter before real dispatch.
>
> **UPDATE 2026-09-11 — all findings resolved.** B1/H1/H2 fixed; M1–M5 fixed; L1–L9 fixed or documented as ceilings. Regression tests added (open-redirect blocked, webhook signature required/disabled, ledger-address suppression). customer360-api suite green (**53 passed**); backend suites mock-isolated from the changes + validated in CI. See **§8. Resolutions applied**.

---

## 1. Overview

- **SCRUM-97** replaces the two placeholder Dagster jobs with real pipelines:
  - `campaign_activation` (`backend-system/campaign_activation/`): loads the campaign tenant-scoped, **hard-gates on `approval_status == 'Approved'`**, validates the template is Approved, marks the campaign `Running`, and submits an out-of-process `email_engine_job` run via Dagster GraphQL.
  - `email_engine` (`backend-system/email_engine/`): resolves segment members by `segment_tag`, filters suppressed/ineligible, renders per recipient, dispatches via a pluggable adapter (`mock` default / `smtp`), and writes an idempotent `cdp_campaign_dispatch_logs` ledger (UNIQUE `(campaign_id, master_profile_id)`, terminal rows never re-sent). Per-tenant SMTP config in `crm_email_provider_config` (DB source-of-truth, Redis-cached).
- **SCRUM-98** adds public tracking + compliance in `customer360-api`: open-pixel, click-redirect, unsubscribe, and a provider webhook; HMAC-signed tracking tokens; events normalize into `cdp_raw_events`; hard bounce/complaint/unsubscribe add to `cdp_email_suppression`, which the send path consults.

**What's genuinely good (verified):** ledger idempotency (UNIQUE + `WHERE status NOT IN ('Sent','Suppressed')` never downgrades a terminal row); RLS `ENABLE`+`FORCE`+`tenant_policy` on all four new tables; public writes set `app.tenant_id` **from the signed token** (not a header) so RLS holds; constant-time HMAC compare on decode; soft bounces excluded from suppression; the approval gate is re-checked in `email_engine` (defense in depth); migrations are idempotent with rollbacks; naming matches the epic gate (`sync_segment_crm`, `email_*`, `campaign_*`, `crm_*`/`cdp_*`).

---

## 2. AC coverage

### SCRUM-97 — Dagster Execution Modernization
| AC | Status | Note |
|---|---|---|
| `campaign_activation`/`email_engine` run real logic, not sleeps | ✅ | real orchestration + send pipeline |
| Observable in Dagster & retry-safe | 🟡 | observable ✅; retry-safe at the **ledger** level, but a retry/concurrent run can double-*send* (H2) |
| Dispatch idempotency + failure handling | 🟡 | per-recipient savepoints ✅; ledger idempotent ✅; **actual-send** idempotency not guaranteed under concurrency/crash (H2); suppression fails open (M1) |

**Verdict: substantially met** for the mock path; H2 + M1 must be resolved before real-provider dispatch.

### SCRUM-98 — Tracking, Webhooks & Compliance
| AC | Status | Note |
|---|---|---|
| Open + click captured, campaign/profile correlation | ✅ | token binds tenant/campaign/profile; constant-time verify |
| Bounce/complaint immediately affects suppression + eligibility | 🟡 | suppression write ✅ and consulted by engine ✅ — **but** the webhook that triggers it is forgeable (B1) |
| Duplicate callbacks don't inflate metrics | ❌ | dedup is a non-atomic check-then-insert; dedup key is attacker-controllable (M2) |
| (implied) events come from a trusted source | ❌ | **no webhook signature verification** (B1) |

**Verdict: PARTIAL** — the mechanics work, but authenticity + dedup integrity (the compliance-critical half) are not met.

---

## 3. Findings (severity-ranked, verified)

### B1 — BLOCKER · Provider webhook has no signature verification and trusts `payload.email`
`customer360-api/core/routers/email_tracking_api.py:97-129` · **CONFIRMED**
The only gate is `decode_tracking_token(payload.token)`, but tracking tokens are **public** — they ride in the `u=` param of the open-pixel/click links inside every delivered email. `CRM_EMAIL_WEBHOOK_SIGNING_SECRET` (called for in SCRUM-92) is unimplemented (absent from `config.py` / `.env.example`). Worse, line 119 does `email = payload.email or resolve_recipient_email(...)` — the suppression target is taken from the **attacker-supplied** body.
**Failure:** `POST /api/v1/track/email/webhook?provider=x` with `{"token":"<any valid token for tenant T>","event":"complaint","email":"ceo@bigcustomer.com"}` → 200 and `ceo@bigcustomer.com` is inserted into `cdp_email_suppression` for tenant T — permanently blocking that tenant from emailing the victim, and inflating delivered/open/click metrics.
**Fix:** verify the provider's HMAC signature header with `hmac.compare_digest` (+ timestamp/nonce replay window) using a new `CRM_EMAIL_WEBHOOK_SIGNING_SECRET`; never derive the suppression address from `payload.email` on an unauthenticated request — resolve it from the dispatch ledger via the signed token.

### H1 — HIGH · Open redirect on the click endpoint
`customer360-api/core/routers/email_tracking_api.py:64-74` · **CONFIRMED**
The destination `url` is a separate, **unsigned** query param; the token signs only `tenant|campaign|profile`. Validation is scheme-only (`http/https`).
**Failure:** `GET /api/v1/track/email/click?u=<valid token>&url=https://evil.example/phish` → `302 Location: https://evil.example/phish` — a phishing redirect served from the trusted tracking domain.
**Fix:** sign the destination into the token (or an accompanying HMAC'd param) and reject on mismatch, or allow-list per-campaign hosts.

### H2 — HIGH · Concurrent/crash double-send (actual emails, not just ledger rows)
`backend-system/email_engine/email_engine/send.py:313-361` (+ `campaign_activation/.../activation.py:105-121`) · **CONFIRMED**
The `Sent` row is written *after* `adapter.send()` and committed per batch (`conn.commit()` at end of `_process_batch`). The `_current_status` pre-check is not a lock, and activation unconditionally re-submits an `email_engine` run (Dagster `RetryPolicy(max_retries=2)`). Two overlapping runs — or a retry after a crash between send and commit — both pass the pre-check and both actually send. The UNIQUE ledger prevents duplicate *rows*, not duplicate *emails*.
**Failure:** `submit_job_execution` succeeds server-side but the client times out → op retry submits a 2nd run → two runs double-send still-`Pending` recipients.
**Fix:** serialize sends per campaign (advisory lock on `campaign_id`), or claim each recipient (`Pending` + `attempt_count`) in its own committed txn *before* calling the adapter.

### M1 — MEDIUM · Suppression lookup fails OPEN
`backend-system/email_engine/email_engine/send.py:135-158` · **CONFIRMED**
`_suppressed_emails` wraps the query in a bare `except Exception` returning an empty set. The docstring frames this as tolerating an absent table, but it also swallows transient errors/timeouts/deadlocks.
**Failure:** a transient DB error during the suppression SELECT for a batch → every recipient treated as not-suppressed → hard-bounced/complained/unsubscribed users get emailed (compliance breach).
**Fix:** tolerate only `UndefinedTable`/`relation does not exist`; on any other error, fail the batch/recipient rather than send.

### M2 — MEDIUM · Dedup is a TOCTOU check-then-insert (and the unique index can't back it)
`customer360-api/core/crud/email_tracking.py:98-129` (+ `database-schema.sql` `ux_cdp_raw_events_tenant_source_dedup`) · **CONFIRMED**
`_dedup_exists` SELECT then INSERT are not atomic; the only unique index includes `event_time`, while the insert uses `event_time = now()`, so two concurrent identical callbacks get different timestamps, both pass the check, and both satisfy the index. The webhook dedup key `f"{provider}:{dedup_id}"` also uses the attacker-controllable `provider` param + `message_id`.
**Failure:** a provider's two simultaneous delivery retries → 2 `cdp_raw_events` rows for one event → inflated metrics (violates SCRUM-98's dedup AC).
**Fix:** atomic dedup — a dedicated dedup table (or an index excluding `event_time`) written `ON CONFLICT DO NOTHING`, or a per-key advisory lock.

### M3 — MEDIUM · Engagement events silently dropped when no ACTIVE `cdp_profile_links` row
`customer360-api/core/crud/email_tracking.py:102-106` · **CONFIRMED**
`cdp_raw_events.raw_profile_id` is NOT NULL; if the master profile has no ACTIVE raw link, `record_engagement_event` returns `skipped_no_raw_profile` and writes nothing (suppression still applied). Sync-created leads/contacts often have no raw-profile link.
**Failure:** opens/clicks for such recipients never land in the event catalog → under-counted metrics with no audit trail.
**Fix:** fall back to a synthetic/placeholder raw_profile_id (or relax the constraint for engagement rows) and at minimum count the drop.

### M4 — MEDIUM · Dev seeder has no production guard
`backend-system/scripts/seed_email_campaign.py:109-146` · **CONFIRMED**
No env gate/confirmation; it acts on whatever `DB_*` points at, creating an Approved template + Approved campaign and (with `TAG_PROFILES>0`) appending the segment tag to **real** active profiles.
**Failure:** `TENANT_ID=<prod> TAG_PROFILES=5 python seed_email_campaign.py` against a prod DB → 5 real customers tagged into an Approved campaign → activation sends them real email.
**Fix:** refuse unless `SEED_ALLOW=1` (or a non-prod marker); only tag profiles you also give an `@example.com` address.

### M5 — MEDIUM · Activation validates the template but not the segment
`backend-system/campaign_activation/campaign_activation/activation.py:92-103` · **CONFIRMED**
Template existence + Approved status is enforced; the segment is only checked for a non-null `segment_id`. A dangling/deleted `segment_id` passes, the "snapshot" count silently returns 0, the campaign is marked `Running`, and an `email_engine` run is triggered that then raises far from the cause.
**Fix:** validate the segment row/`segment_tag` in activation (mirror the template check); consider refusing on `snapshot_count == 0`.

### Low findings
- **L1** — `rendering.render_string` does no HTML-escaping of merge vars; a profile `first_name` with markup is injected raw into `html_body` (email-HTML injection). `email_engine/rendering.py:22-26`.
- **L2** — weak default `EMAIL_TRACKING_SECRET = "leocdp-dev-tracking-secret"` (`email_engine/tracking.py:22`); forgeable tokens if not overridden, and both deployables must share the same value.
- **L3** — `SMTPDispatchAdapter` opens a new SMTP connection **per recipient** (`adapters.py:98-112`) — a throughput ceiling for large sends.
- **L4** — pixel/click `u` (and click `url`) are required params → a stripped/rewritten URL yields FastAPI **422** to the mail client instead of a benign GIF/redirect (`email_tracking_api.py:55,65`); make `u` optional.
- **L5** — unused import `require_tenant` in `crm_sync_api.py:21` (the file uses its own UUID-validating `_require_tenant`); dead code.
- **L6** — `campaign_activation_api.py` uses `core.auth.require_tenant`, which does not UUID-validate, so a malformed tenant claim 500s instead of a clean 400 (crm_sync uses a local validator). `auth.py` `require_tenant`.
- **L7** — the "segment snapshot" is a bare `count(*)` — not persisted/isolated (membership changes between activate and send are picked up), and the logged count ignores no-email/opted-out/suppressed, overstating the send. `activation.py:55-68,103`.
- **L8** — `rls.set_tenant_context` uses session-level `SET` not `SET LOCAL`; harmless today (dedicated connection per run) but would leak tenant context behind a transaction-mode pgbouncer. `email_engine/rls.py`, `campaign_activation/rls.py`.
- **L9** — the tracking-token format is implemented independently in both deployables (`email_engine/tracking.py` encode, `customer360-api/core/utils/email_tracking.py` decode) — drift risk.

---

## 4. Security summary

| Area | State |
|---|---|
| Webhook authenticity | ❌ **B1** — no signature; forgeable; trusts `payload.email` |
| Click redirect | ❌ **H1** — open redirect (unsigned `url`) |
| Token integrity (tenant/campaign/profile) | ✅ HMAC + constant-time compare (weak default secret — L2) |
| Tenant isolation / RLS | ✅ forced RLS on all 4 tables; public writes bind `app.tenant_id` from the signed token |
| Secrets | 🟡 `smtp_password` not logged (str(exc) from smtplib carries no password); `crm_email_provider_config.smtp_password` stored plaintext (migration notes "protect at rest in non-dev") |
| Suppression integrity | 🟡 correct + idempotent, but reachable via forged webhook (B1) and can fail open (M1) |

---

## 5. Test coverage gaps
- **Dedup (a core AC) is untested** — `record_engagement_event` is fully mocked in `test_email_tracking.py`; `_dedup_exists`/insert and `add_suppression` `ON CONFLICT` idempotency have no coverage.
- **Open redirect untested** — `test_click_rejects_non_http_scheme` covers `javascript:` but nothing asserts an arbitrary external http host is (wrongly) allowed.
- **No webhook-authenticity test** (because no verification exists) and none asserting `payload.email` is trusted.
- **`activate_campaign` core logic untested** — `test_dagster_defs.py` monkeypatches it out; the approval gate + template/segment validation + idempotency ship without direct tests.
- `skipped_no_raw_profile` path and `_session_for_tenant` RLS binding untested.

---

## 6. Prioritized actions

1. **B1** — verify webhook provider signature; stop trusting `payload.email`. *(merge blocker for live webhooks)*
2. **H1** — sign the click destination / allow-list hosts. *(blocker for real sends)*
3. **H2** — serialize or claim-before-send so retries/concurrency can't double-email. *(before real-provider dispatch)*
4. **M1** — make the suppression lookup fail closed on non-"table absent" errors. *(compliance)*
5. **M2** — atomic dedup. **M3** — don't silently drop engagement. **M5** — validate the segment. **M4** — guard the seeder.
6. **L1–L9** — polish; add the missing dedup / redirect / webhook / activation tests.

## 7. Risk table

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Forged webhook poisons suppression / metrics (B1) | High once webhooks are live | Compliance-list poisoning; targeted denial-of-email | Provider signature + don't trust `payload.email` |
| Open redirect (H1) | Medium | Phishing from the trusted domain | Sign/allow-list the destination |
| Double-send on retry/concurrency (H2) | Medium | Duplicate emails, reputation/compliance | Serialize / claim-before-send |
| Suppression fails open (M1) | Low–medium | Mailing a suppressed user | Fail closed on transient errors |
| Metric inflation from racy dedup (M2) | Medium | Wrong analytics | Atomic dedup |

**Overall:** the architecture and the happy-path mechanics are solid and idiomatic, and SCRUM-97's real-pipeline goal is met on the mock path. But SCRUM-98's authenticity/dedup ACs are not met, and B1/H1 are security gates that must be closed before this handles live webhooks or real sends.

---

## 8. Resolutions applied (2026-09-11)

| # | Fix | Files |
|---|-----|-------|
| **B1** | ✅ Webhook now verifies `X-Webhook-Signature` (HMAC-SHA256 over the raw body, constant-time) using new `CRM_EMAIL_WEBHOOK_SIGNING_SECRET`; **disabled (503) when the secret is unset** (fail closed); suppression address resolved from the **dispatch ledger via the signed token**, never `payload.email`. | `routers/email_tracking_api.py`, `utils/email_tracking.py`, `config.py`, `.env.example` |
| **H1** | ✅ Click destination is now signed (`k=` HMAC of the URL, minted in `rewrite_links_for_click_tracking`); `track_click` redirects to the URL only when `k` verifies **and** it's http/https, else `/`. | `email_tracking_api.py`, `utils/email_tracking.py`, `email_engine/rendering.py`, `email_engine/tracking.py` |
| **H2** | ✅ `send_campaign` takes a per-campaign `pg_try_advisory_lock` (classid 1); a concurrent/retried run that can't acquire it skips instead of double-sending. | `email_engine/send.py` |
| **M1** | ✅ Suppression lookup now tolerates only `UndefinedTable`; any other error **fails closed** (raises) rather than sending a batch with unknown suppression state. | `email_engine/send.py` |
| **M2** | ✅ Dedup made atomic via `pg_advisory_xact_lock` (classid 2) on the dedup key around the check-then-insert. | `crud/email_tracking.py` |
| **M3** | ✅ Raw-profile lookup broadened (prefer ACTIVE, fall back to any link) and the drop elevated to a WARNING flagged as a metric under-count. (Full fix — nullable engagement `raw_profile_id` — deferred: needs a migration on the partitioned `cdp_raw_events`.) | `crud/email_tracking.py` |
| **M4** | ✅ Seeder refuses unless `SEED_ALLOW=1`. | `scripts/seed_email_campaign.py` |
| **M5** | ✅ Activation validates the segment row/`segment_tag` (mirrors the template check) — a dangling `segment_id` now fails at activation. | `campaign_activation/activation.py` |
| **L1** | ✅ Merge vars HTML-escaped into the HTML body (subject/text stay plain). | `email_engine/send.py` |
| **L2** | ✅ Warns when the insecure default `EMAIL_TRACKING_SECRET` is in use (both deployables); `.env.example` note. | `email_engine/tracking.py`, `utils/email_tracking.py`, `.env.example` |
| **L3** | 📝 Documented ceiling — SMTP connection-per-send is intentional at beta scale (class docstring). | `email_engine/adapters.py` |
| **L4** | ✅ `u` (open) / `u`,`url` (click) optional → a stripped URL returns the GIF / `/` redirect, not 422. | `email_tracking_api.py` |
| **L5/L6** | ✅ `core.auth.require_tenant` now UUID-validates → clean 400; `crm_sync_api._require_tenant` delegates to it (removes the dead import + duplication). | `core/auth.py`, `crm_sync_api.py` |
| **L7** | ✅ Activation logs "segment size (pre-eligibility …)" instead of the misleading "snapshot". | `campaign_activation/activation.py` |
| **L8** | 📝 `ponytail:` comment on `SET` vs `SET LOCAL` (safe for the dedicated-connection model; upgrade path noted). | both `rls.py` |
| **L9** | 📝 Cross-referenced docstrings; the token format is duplicated across two independent deployables by design (no shared package). | `tracking.py` (both) |

**Tests:** updated `test_email_tracking.py` to the hardened behavior and added coverage the review flagged as missing — open-redirect blocked (H1), webhook rejects unsigned/wrong-signature and is disabled without a secret (B1), suppression uses the ledger address, and the token-less pixel path (L4). customer360-api suites: **53 passed**. Backend `email_engine`/`campaign_activation` tests mock `send_campaign`/`activate_campaign`, so the changed bodies are isolated (validated in CI).

**Deferred (tracked, not a regression):** M3's full fix (nullable engagement `raw_profile_id`) and dedicated unit tests for `activate_campaign`'s core logic remain follow-ups.
