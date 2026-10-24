# Ad personalisation

How the ad server ranks ads for the individual visitor, how visitors and the business stay in control, and how to run it. The design lives in `specs/003-ad-personalization-engine/` (`spec.md`, `plan.md`, `data-model.md`, `research.md`, and the API contracts under `contracts/`); this page is the operating guide.

## What it does

When a visitor's browser asks for ads, the ad server:

1. **Checks eligibility** for every candidate ad. This always happens, for every visitor: the ad and creative must be active and inside their validity windows, the campaign active and inside its dates, no targeting rule may exclude the request (on a known mismatch only), and the visitor must be under the ad's frequency cap.
2. **Decides whether to personalise** (settings, consent, hold-out, then whether there is anything to personalise with).
3. **Ranks** the eligible ads. A score is the product of named factors, so every rank can be explained: how well the ad does on its own campaign objective compared with the placement's usual, a measured amount of exploration, how the visitor has responded to similar ads, the ad's configured weight, and, for visitors with little history, how similar visitors responded and whether the page's keywords match.

Anything that goes wrong after the ads are loaded (Redis, the database, running out of time) returns the eligible ads in default order, never an error.

## Consent modes

Personalisation uses a visitor's behaviour, so it only happens for visitors who may be profiled. Each tenant picks one mode:

| Mode | A visitor with no recorded choice | `granted` | `denied` |
|---|---|---|---|
| **opt-in** (the default) | Not personalised | Personalised | Not personalised |
| **opt-out** | Personalised | Personalised | Not personalised |

- A visitor's choice is recorded by the widget (`PUT /consent`) or reported with a serve request (`consent=granted|denied`). Every change is kept in an audit table with its time and source.
- A denial takes effect immediately: the next request is not personalised, the visitor's fresh state in Redis is cleared, and no further events are attached to their key. The next hourly profile rebuild deletes their stored profile.
- Switching a tenant's mode keeps everyone's recorded choices. Visitors with no recorded choice follow the new mode, and a profile built earlier is never used for a visitor who is no longer eligible.
- A visitor who may not be profiled still sees ads, and their events still count towards delivery statistics, but those events are stored **without a visitor key**.

## Turning it on

Personalisation ships **off**. Switch it on per tenant, site or placement with the admin call (tenant admins only; the tenant comes from the caller's token):

```
PUT /ad-personalization/settings
{ "scope_type": "tenant", "scope_ref": "", "enabled": true, "holdout_pct": 10, "consent_mode": "opt_in" }
```

- `scope_type` is `tenant`, `site` (`scope_ref` is the site key the widget sends) or `placement` (`scope_ref` is the placement key). The most specific setting wins, so one placement can be off while the rest stay on, and a site can opt out of a tenant-wide switch-on.
- `consent_mode` and the hold-out salt are tenant-wide, taken from the tenant row.
- **Hold-out** (`holdout_pct`, 0 to 100, default 10): that share of visitors always gets default ranking, chosen by a stable hash so a visitor stays in the same group for a measurement period. Every serving decision records its group. Changing the salt starts a new measurement period.
- Settings are cached for up to a minute (`c360_PROMOTION_SETTINGS_CACHE_TTL_SECONDS`).
- Visitor-level state (profiles, frequency counters, fresh signals) is only kept for scopes where personalisation is on. Frequency caps therefore only apply to visitors once it is on.

## Erasure

A tenant admin can erase a visitor's personalisation data:

```
POST /ad-personalization/erasure-requests     { "leovid": "<visitor id>", "urgent": true }
GET  /ad-personalization/erasure-requests/{erasure_id}
```

(`profile_ref` can be given instead of `leovid` to erase every device linked to a known visitor; exactly one of the two is required.)

1. The request is recorded (only hashed keys, never the visitor id) and the visitor's Redis state is purged at once. Status `pending`, then `redis_purged`.
2. The hourly erasure job deletes the visitor's events (every partition), serving decisions, profile, consent record, consent history and serving key, records how many rows it removed, and sets `completed`. A visitor with no data still ends `completed`, as evidence.
3. A failed run is marked `failed` with the reason and retried on the next run. Profile and cohort jobs skip a visitor with an open request, so nothing is rebuilt in the meantime.

**Limits:** an urgent request must be completed within **72 hours** and any other within **30 days**. The job logs an error for any request past its limit (and still processes it).

## Reading an explanation and the comparison report

```
GET /ad-personalization/decisions/{request_id}
GET /ad-personalization/decisions?ad_key=…           (recent decisions that included an ad)
GET /ad-personalization/reports/comparison?from=…&to=…
```

- A **decision** shows the group, the placement, how many candidates there were, and for each ad served its rank, score and its **signals**: `objective_rate_lift`, `exploration`, `visitor_affinity`, `base_weight`, and for visitors with little history `cohort_pattern` and `page_context`. Each signal is a multiplier (above 1 raised the ad, below 1 lowered it) with a plain-language note, for example "click rate 2.60% vs placement baseline 2.00%". The visitor appears only as a short fingerprint, for correlation. Only the ads that were served are stored, not every candidate.
- The **comparison report** sets personalised delivery against the hold-out group, per campaign objective, on the metric that objective is judged by (views for awareness, clicks for traffic, conversions for conversion campaigns, clicks otherwise). It shows both rates and the lift, or `insufficient_data` when a group is too small to say (fewer than 1,000 impressions per group and objective by default; `AD_PERSONALIZATION_REPORT_MIN_IMPRESSIONS` changes it). The same data feeds the **Ad personalisation** tab in the admin frontend.

## Eligibility rollout

Eligibility (targeting rules, frequency caps, validity windows and campaign dates) is enforced on **all** delivery, including for tenants that never switch personalisation on. Before this feature the ad server returned every active ad for a placement without applying those rules, so enabling it can remove ads that used to appear. **Release note:** *targeting rules, frequency caps, validity windows and campaign dates are now enforced on all ad delivery.*

Recommended order for a rollout:

1. Apply the schema (`sql-scripts/personalization-schema.sql`; `start.sh` does this).
2. Deploy the ad server with eligibility enforced, and check delivery for each tenant against what their campaign owners expect.
3. Deploy event capture (the widget and `POST /events`) and the batch jobs.
4. Switch personalisation on, tenant by tenant, with a hold-out.

**Kill switch:** `c360_PROMOTION_ELIGIBILITY_ENFORCED=false` restores the previous behaviour (no eligibility applied at all) without a code change. It is a rollback aid, not a configuration to leave on: with it off, paused or ended campaigns are served and frequency caps are ignored. A test (`tests/personalization/test_eligibility_killswitch.py`) proves that delivery with the switch off matches the pre-feature path.

## Security and privacy notes

- Visitor ids are never stored. The server stores a keyed hash (HMAC-SHA-256 with a per-tenant secret derived from `c360_PROMOTION_VISITOR_KEY_MASTER_SECRET`, truncated to 16 bytes), so the stored key cannot be guessed from a visitor id and the same visitor has different keys in different tenants. The admin API derives the same key from the same secret (`AD_VISITOR_KEY_MASTER_SECRET`, or the ad server's variable) so erasure finds the right rows; shared test vectors keep the implementations identical.
- Logs never contain a raw visitor id or a full visitor key, only a short fingerprint. The access log redacts `leovid=` values, database errors hide bound parameters, and failures that involved a visitor key log exception types only.
- Stored events keep only the page address without its query string, fragment or credentials.
- `/events` and `/consent` are rate limited per visitor (`EVENT_RATE_LIMIT_PER_MINUTE`, `CONSENT_RATE_LIMIT_PER_MINUTE`) and answer `429` with `Retry-After`. The browser-facing endpoints accept cross-origin requests from any site, without credentials.
- Declared crawlers (by user agent) are flagged and excluded. Behavioural bot detection is **not** part of this feature.

### Rotating secrets

- **`visitor_key_master_secret`: rotating it is a reset, not a routine change.** Every stored visitor key, consent record and profile was derived from it, so after a rotation no existing visitor matches their stored data: history and recorded consent are effectively lost (under opt-in, nobody is eligible until they consent again). Treat it as a one-off decision, schedule it, announce it, and expect the hold-out and statistics to restart. Set the same new value on the ad server, the API and the Dagster jobs at the same time. If a leak is suspected, rotating is the right response; routine rotation is not recommended.
- **Conversion secrets** (`c360_PROMOTION_CONVERSION_SECRETS`, one per tenant): a tenant has one secret at a time, so change it in the sender and the ad server together; postbacks signed with the old secret are rejected as soon as the ad server restarts with the new one. Never log either secret.

## Known limits

- Only declared crawlers are filtered (see above).
- The source of conversions is defined outside this feature: conversions are only attributed when an advertiser or affiliate system posts them to `/conversions` signed with the tenant's secret.
- A link between devices (cross-device history) is read from the platform's master profiles by an hourly job and cached for up to a minute per visitor, so an unlink can take a minute to be felt.
- A signal within two minutes of an hourly profile rebuild can be counted twice for a short time (a small, bounded overstatement chosen over losing the signal).
- The 25 ms personalisation budget is a default; under heavy load requests fall back to default ranking by design. Measure it in your environment (below).

## Load and latency check

`customer360-promotions/tests/load/serve_load.py` drives `/serve` and `/events` at a set rate and reports the latency personalisation adds against requests with no visitor id, the event intake status codes and `Retry-After` headers. It needs no dependency beyond the tests' HTTP client.

```
python tests/load/serve_load.py --base-url http://localhost:9009 --tenant demo \
    --placement homepage_top --rps 200 --duration 30 --visitors 500
```

Run it against a disposable database with personalisation on for the tenant and a hold-out of 0. The target is at least 95% of `/serve` calls no more than 50 ms slower than the baseline, and every response valid; the script prints `PASS` or `FAIL` and exits non-zero on failure. Add `--json` for a machine-readable report.
