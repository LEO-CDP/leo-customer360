# Code Review (unified: max correctness/security + ponytail) — SCRUM-97 & SCRUM-98

**Command:** `/code-review max` + ponytail over-engineering pass (unified report)
**Date:** 2026-09-13
**Branch:** `feat/SCRUM-92/subtask-05-06` (HEAD `d605185`, includes a `main` merge)
**Base:** `feat/SCRUM-92-leo-cdp-v-2-0-beta-agentic-outbound-email-marketing-execution-engine` (merge-base `0ae515b`)
**Scope:** the SCRUM-97/98 change set only (~44 files) — email execution (`backend-system/email_engine`, `campaign_activation`), tracking/compliance (`customer360-api` routers/crud/utils), migrations 003/004 + fresh schema, and the e2e suite. The main-merge churn (`tools/docs-vector-search`, most `deployments/*`, research papers, `dev-c360.sh`) is **out of scope** for this review.
**Tickets:** [SCRUM-97](https://leocdp.atlassian.net/browse/SCRUM-97) · [SCRUM-98](https://leocdp.atlassian.net/browse/SCRUM-98)
**Method:** this is a **re-review of the post-fix code** (the 2026-09-11 review's B1/H1/H2/M1–M5/L1–L9 were addressed). All prior fixes were adversarially re-verified (independent pass, tried to find bypasses); DB parity + tests checked first-hand.

> **Verdict: all findings resolved — ship-ready for the feature; lean and hardened.** Every prior blocker/high/medium holds under adversarial verification, and this pass's new findings (N1–N6) are now fixed or documented as accepted ceilings (§8). The ponytail cut (PT1) removed the Redis provider-config cache — which **also closed the secret-in-Redis medium N2** — for ~75 fewer lines; PT2 was withdrawn (schema already minimal). Suites green (customer360-api 53, email_engine 16). **Operational reminder:** set `CRM_EMAIL_WEBHOOK_SIGNING_SECRET` and a non-default `EMAIL_TRACKING_SECRET` in prod before enabling webhooks / real sends.

---

## 1. Prior findings — all resolved (verified)

| # | Prior finding | Status | Evidence (adversarially verified) |
|---|---|---|---|
| B1 | Forgeable webhooks poison suppression | ✅ HOLDS | HMAC over the raw body (no re-serialize TOCTOU), constant-time, **503 when secret unset**, 401 on bad sig; `payload.email` never read — suppression address resolved from the ledger via the signed token. Forgery needs both the webhook secret **and** a tracking token (separate secrets). |
| H1 | Open redirect on click | ✅ HOLDS | 302 to `url` requires http/https **and** `verify_click_url(url,k)`; engine `k` + endpoint verify use the same secret over the same decoded URL; `//evil`, `https:/\evil`, case/whitespace/encoding all fail; `k` unforgeable without the secret. |
| H2 | Concurrent/retry double-send | ✅ HOLDS | `pg_try_advisory_lock(1, hashtext(campaign_id))` before any send, released in `finally`; not-acquired → clean `skipped_locked`; distinct classid from the dedup lock (2) — no collision. |
| M1 | Suppression fail-open | ✅ HOLDS | only `errors.UndefinedTable` tolerated; any other error rolls back + `raise`s; caller chain has no swallow → batch aborts before any send. |
| M2 | Dedup TOCTOU | ✅ HOLDS | `pg_advisory_xact_lock(2, hashtext(dedup_key))` serializes the check-then-insert. |
| M3 | Events dropped w/o raw-profile | ✅ (partial) | lookup broadened (prefer ACTIVE, fall back to any) + WARN; full nullable-column fix deferred. |
| M4 | Seeder prod guard | ✅ | `SEED_ALLOW=1` required. |
| M5 | Activation segment validation | ✅ HOLDS | segment row/`segment_tag` validated at activation. |
| L1/L4/L5/L6 | escape / optional params / dead import / tenant UUID | ✅ | all applied. |
| L2/L3/L7/L8/L9 | secret warning / SMTP conn / naming / SET LOCAL / token dup | ✅ documented | ceilings noted (L2 see N4 below — the warning is arguably not enough). |

**Verified clean:** `EmailProviderConfigRead` omits `smtp_password` (only `smtp_password_set`); `_to_provider_read` strips it on GET **and** PUT (unit-tested: `test_put_provider_config_hides_password`); all activation/config/dispatch routes are `require_tenant` + `require_tenant_admin`; RLS `FORCE` + `tenant_policy` on all 3 new tables in both the migration **and** fresh `database-schema.sql`; the `uq_*` constraints that `ON CONFLICT`/cache rely on all exist; no `smtp_password` in any log line.

---

## 2. AC coverage (post-fix)

**SCRUM-97 — MET (mock path).** Real `campaign_activation` + `email_engine` pipelines; Dagster-observable; retry-safe (H2 lock); dispatch idempotent (ledger UNIQUE + terminal guard); failure handling (per-recipient savepoints, fail-closed suppression). Caveat: **N1** (SMTP TLS) must be fixed before a real SMTP provider.

**SCRUM-98 — MET.** Open/click captured + correlated; **authenticated** webhook (B1); atomic dedup (M2); bounce/complaint → suppression, consulted by the send eligibility query. Caveat: **N4** — the tracking-token secret must be rotated off the dev default in prod or token integrity degrades.

---

## 3. New findings (this pass)

### N1 — MEDIUM · SMTP `starttls()` uses no SSL context (unverified TLS) — CONFIRMED
`backend-system/email_engine/email_engine/adapters.py:104`
`server.starttls()` is called with no `context`, so `smtplib` uses an unverified context (`check_hostname=False`, `verify_mode=CERT_NONE`). A MITM on the SMTP path can intercept the channel and capture `server.login(username, password)` — i.e. the tenant's `smtp_password` — and message content.
**Fix:** `server.starttls(context=ssl.create_default_context())`.

### N2 — MEDIUM · Redis caches `smtp_password` in plaintext — PLAUSIBLE
`backend-system/email_engine/email_engine/provider_config.py:113`
The full resolved config (including `smtp_password`) is `json.dumps`'d into Redis via `setex`. The migration notes "protect at rest", but the cache copy is undocumented cleartext in a Redis both deployables share and whose `REDIS_PASSWORD` is optional (may be unauthenticated in dev).
**Fix:** cache only non-secret fields and re-read `smtp_password` from the DB at send time, or encrypt the cached blob.

### N3 — MEDIUM · Provider-config PUT clobbers omitted fields (except password) — CONFIRMED
`customer360-api/core/crud/email_provider.py:77-81`
`payload.model_dump(exclude_unset=False)` means a partial PUT resets every omitted field to its schema default (`provider→"mock"`, `smtp_host/username/from_address→NULL`) while `smtp_password` alone is preserved-on-omit. A "just toggle `is_active`" PUT silently drops the tenant back to the mock/env sender.
**Fix:** make PUT an explicit full-replace (drop the password special-case) **or** merge all fields with `exclude_unset=True`.

### N4 — LOW-MEDIUM · `EMAIL_TRACKING_SECRET` keeps a well-known default and doesn't fail closed — PLAUSIBLE
`customer360-api/core/config.py:189-192`, `backend-system/email_engine/email_engine/tracking.py:24`
Unlike the webhook secret (empty default → 503), the tracking secret defaults to the public repo value `leocdp-dev-tracking-secret` and only logs a warning. Left at default in prod, anyone can mint valid click `k` (H1 returns) and forge tracking tokens (fake opens/clicks/unsubscribes, self-suppression).
**Fix:** in prod, refuse to sign / disable the feature when the secret equals the dev default (mirror the webhook's fail-closed stance).

### N5 — LOW · Residual single-recipient at-least-once window — PLAUSIBLE
`backend-system/email_engine/email_engine/send.py:380-388`
Within a recipient's SAVEPOINT, `adapter.send()` runs before `_upsert_dispatch`; if the upsert then raises, the savepoint rollback discards the ledger row while the email already went out → a later run re-sends. Not an H2 bypass (concurrent runs are serialized); a narrow crash/DB-error window. **Fix:** record an intent/attempt row before dispatch, or accept as documented at-least-once.

### N6 — LOW · Advisory-lock key is 32-bit `hashtext` — PLAUSIBLE
`backend-system/email_engine/email_engine/send.py:273`
Two different campaign ids colliding under `hashtext`, sent concurrently, cause one to `skipped_locked` (under-send, not double-send) until re-activated. Astronomically rare. **Fix:** acceptable for beta; note the ceiling.

---

## 4. Tests

- **customer360-api unit: green** — `test_email_tracking.py` now covers the hardened behavior (open-redirect blocked, webhook unsigned→401 / wrong-sig→401 / disabled→503, ledger-address suppression) and `test_campaign_activation_router.py` covers the approval gate (409), 404/cross-tenant, submit-when-approved, and **password-hiding on PUT**.
- **email_engine unit: green** — rendering (`&k=`), `_process_batch` eligibility/idempotency, adapters.
- **E2E** (`tests/e2e/test_campaign_activation_e2e.py`, `test_email_tracking_e2e.py`): 15 cases, **deploy-gated** (skip until this branch is on the target; UAT still runs a pre-05/06 build). Zero-footprint (synthetic tokens).
- **Gaps:** the backend `activate_campaign` orchestration is still only exercised via the mocked Dagster wiring test (M5's validation has no direct unit test); no unit test asserts the SMTP TLS context (N1).

---

## 5. Prioritized actions

1. **N1** — pass `ssl.create_default_context()` to `starttls()`. *(credential exposure; before real SMTP)*
2. **N4** — fail closed / disable when `EMAIL_TRACKING_SECRET` is the dev default in prod.
3. **N2** — stop caching `smtp_password` in plaintext (cache non-secrets, re-read at send).
4. **N3** — fix PUT merge semantics (full-replace or `exclude_unset=True`).
5. **N5/N6** — document the at-least-once + lock-collision ceilings; **operational**: set `CRM_EMAIL_WEBHOOK_SIGNING_SECRET` before enabling webhooks.
6. Add a direct unit test for `activate_campaign` (approval + segment/template validation).

## 6. Risk summary

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| SMTP creds captured via unverified TLS (N1) | Medium (real SMTP + MITM) | Tenant SMTP password + mail content leak | `starttls(context=create_default_context())` |
| SMTP password read from Redis (N2) | Low-medium (shared/dev Redis) | Credential leak | Don't cache the secret / encrypt |
| Provider config silently reset by partial PUT (N3) | Medium (normal API usage) | Tenant drops to mock sender | Full-replace or merge semantics |
| Forged tokens if tracking secret left default (N4) | Medium (if not rotated) | Open-redirect returns, fake engagement, self-suppression | Fail closed on default in prod |

**Overall:** the implementation is now solid and the security-critical ACs are met — the earlier blocker/high issues are genuinely closed and hold under adversarial re-testing. What remains is real-SMTP + prod-secret hardening (N1–N4), all medium-or-lower, none blocking the mock execution path or the mergeability of the feature.

---

## 7. Over-engineering review (ponytail)

Scope: over-engineering/complexity only (correctness/security are §1–§6). Format: `location: what to cut → what replaces it`.

The feature is lean for what it does — explicit route constants, stdlib-only rendering (deliberately **not** a template engine — good call), small single-purpose helpers, reused segmentation/SQL-safety modules. Only two real cuts:

- **PT1 — `delete:` the Redis provider-config cache.** `backend-system/email_engine/email_engine/provider_config.py` — `load_email_config` is called **exactly once per run** (`send.py:286`), so the Redis tier saves one DB read per campaign send while adding a whole cache/TTL/invalidation layer **and** the N2 plaintext-secret-in-Redis vector. → Read the active `crm_email_provider_config` row from the DB each run (DB → mock fallback). Net ~**−40 lines**, one fewer dependency edge, and **N2 disappears** (secret never leaves the DB). *This is the ponytail + security win — do PT1 instead of N2's "encrypt the cache".*
- **PT2 — ~~`delete:` unused schema classes~~ → WITHDRAWN (false finding).** On inspection `customer360-api/core/schemas/crm.py` has **only** `EmailProviderConfigUpsert` + `EmailProviderConfigRead` (both used) — the `Base/Create/Update` triad never existed (the earlier "0 references" meant *absent*, not *dead*). The schema is already minimal; nothing to cut.

Kept deliberately (not over-engineering):

- **`DispatchAdapter` base + `build_adapter` factory** (`adapters.py`) — a base class + factory for two impls (mock/SMTP) is borderline, but SCRUM-97 explicitly plans SES ("adding a provider = one subclass"), so it's sanctioned foundation, not speculation. Keep.
- **Cross-deployable duplication** — the tracking-token format, `rls.py`, and `db.py` are replicated in `email_engine` + `campaign_activation` because they're independently-deployed code locations with their own `requirements.txt` (no shared package). Forced duplication; can't DRY without new packaging infra. Accept (documented, L9/L8).
- After PT1, the config resolution collapses from three tiers (Redis → DB → env/mock) to two (DB → mock) — simpler as a side effect.

```
net: ~-75 lines cut — PT1 (delete the Redis provider-config cache across
     provider_config.py + email_provider.py, which also fixes N2). PT2 was
     withdrawn (schema already minimal). Everything else earns its place: the
     adapter pattern is ticket-sanctioned foundation, and the cross-deployable
     copies are forced by the independent-code-location model.
```

## 8. Resolutions applied (2026-09-13)

All correctness findings + the actionable ponytail cut are resolved. Suites green: **customer360-api 53 passed**, **email_engine 16 passed**.

| # | Fix | Files |
|---|-----|-------|
| **PT1 / N2** | ✅ Deleted the Redis provider-config cache; the active row is read from the DB **once per run** (SMTP password never leaves Postgres). Resolves the ponytail finding **and** the secret-in-Redis medium in one cut (`~-75 lines`). | `email_engine/provider_config.py`, `crud/email_provider.py` |
| **N1** | ✅ `starttls(context=ssl.create_default_context())` — verified TLS (cert + hostname); no more MITM window on the SMTP login. | `email_engine/adapters.py` |
| **N3** | ✅ Provider-config PUT merges (`exclude_unset=True`) — a partial PUT no longer resets omitted fields; dropped the no-op `metadata_` clobber. | `crud/email_provider.py` |
| **N4** | ✅ `decode_tracking_token` + `verify_click_url` fail closed when `EMAIL_TRACKING_SECRET` is the dev default in a prod `ENVIRONMENT` (mirrors the webhook; UAT/dev keep working on the default). | `utils/email_tracking.py` |
| **PT2** | ➖ Withdrawn — schema already minimal (no dead classes existed). | — |
| **N5 / N6** | 📝 Documented as accepted ceilings (email at-least-once; 32-bit advisory-lock-key collision). | `email_engine/send.py` |

**N5/N6 test coverage:** added `campaign_activation/tests/test_activation.py` — 9 cases covering the happy-path Running+handoff, `trigger_email=False`, and every validation gate (not-found / not-Approved / no template / no segment / template-not-Approved / dangling segment / segment-without-tag), with `connect()` and the email-engine trigger mocked. Suites now: customer360-api 53 (email/router) + email_engine 16 + campaign_activation 9.
