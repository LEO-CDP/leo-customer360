# End-to-end tests (SCRUM-93 / SCRUM-94) — run against UAT

These tests drive a **live** deployment over HTTP (unlike the hermetic unit
suite in `tests/`, which mocks the DB/Redis/Keycloak). They are **skipped**
whenever `E2E_BASE_URL` is unset, so `./run_unit_tests.sh` / `pytest tests/`
stay green without a deployment.

- `test_crm_sync_e2e.py` — SCRUM-94: `POST /api/v1/admin/crm/sync-segment/{id}`
  (dry-run, real sync, idempotent replay), the `sync-runs` audit endpoints, the
  404/400 guards, the auth boundary, and opt-in cross-tenant/non-admin checks.
- `test_schema_foundation_e2e.py` — SCRUM-93: the `crm_campaign` email-marketing
  columns round-trip, the `approval_status` validation, and that
  `crm_segment_sync_runs` is queryable + tenant-scoped.
- `test_campaign_activation_e2e.py` — SCRUM-97: campaign approval, activation,
  dispatch-log, and provider-config endpoints on customer360-api.
- `test_email_tracking_e2e.py` — SCRUM-98: open, signed click, unsubscribe, and
  fail-closed webhook checks on customer360-event-api.

## Configuration (environment variables)

| Var | Required | Default | Meaning |
|-----|----------|---------|---------|
| `E2E_BASE_URL` | **yes** | — | Deployment root, e.g. `https://<uat-host>:8008`. Unset ⇒ whole suite skips. |
| `E2E_TRACKING_BASE_URL` | no | — | Data-tracking deployment root, e.g. `https://beta.leocdp.com/data`. Enables SCRUM-98. |
| `E2E_TRACKING_API_PREFIX` | no | `/api/v1` | API prefix appended to `E2E_TRACKING_BASE_URL` for email routes. |
| `E2E_TENANT_ID` | yes* | — | Tenant UUID used in request bodies + `X-Tenant-Id`. *Required for the create-based tests; must equal the token's tenant in SSO mode. |
| `E2E_BEARER_TOKEN` | SSO on | — | `Authorization: Bearer` JWT carrying `tenant_id`/`user_id` and a **tenant-admin** role. |
| `E2E_BEARER_TOKEN_NON_ADMIN` | no | — | Non-admin JWT used by S94-12 to verify the 403 authorization boundary. |
| `E2E_USER_ID` | no | — | `X-User-Id` (SSO-off mode). |
| `E2E_SEGMENT_ID` | no | — | Reuse an existing segment instead of creating an ephemeral one. |
| `E2E_SEGMENT_SQL` | no | `engagement_score < -1` | `sql_rules` of the ephemeral segment. Default matches **zero** profiles ⇒ a real sync writes no `crm_*` rows (safe on UAT). Set a real predicate for non-zero routing. |
| `E2E_API_PREFIX` | no | `/api/v1` | API path prefix. On Git Bash keep it **unset** (MSYS rewrites leading-slash env values); `test.sh` guards this. |
| `E2E_VERIFY_TLS` | no | `true` | Set `false` to skip TLS verification (self-signed UAT cert). |
| `E2E_TIMEOUT` | no | `60` | Per-request timeout (seconds). |
| `E2E_ALLOW_DATA_WRITES` | no | (off) | `1` enables the opt-in routing tests (`test_routing_e2e.py`) that write to `crm_*` tables. Off by default. |
| `E2E_TENANT_ID_B` / `E2E_BEARER_TOKEN_B` | no | — | A second tenant's id + token; enables the real cross-tenant isolation checks (S94-13/14). |
| `E2E_EMAIL_TRACKING_SECRET` | no | `leocdp-dev-tracking-secret` | HMAC secret for minting email tracking tokens; must match customer360-event-api's `EMAIL_TRACKING_SECRET`. Used by SCRUM-98. |
| `E2E_CAMPAIGN_ID` | no | — | An existing **Approved** campaign (with template + segment); enables the opt-in real activation test S97-07. |

> **SCRUM-98 tracking is separately deploy-gated:** set `E2E_TRACKING_BASE_URL` to the data-tracking deployment. The tests probe `GET /api/v1/track/email/open` there and skip when that service is unavailable. Select them with `CASES=S98 ./test.sh`.

> **Some campaign-governance checks are deploy-gated too:** the live UAT target can lag the source branch. When generic `POST /campaigns` still accepts non-Draft approval states, the SCRUM-93 draft-only guard case skips instead of failing the whole E2E run; the source-of-truth behavior is still enforced by the unit suite.

**Auth modes** (see `core/auth.py`):
- **SSO on** — set `E2E_BEARER_TOKEN` (+ `E2E_TENANT_ID` matching the token).
- **SSO off** — set `E2E_TENANT_ID` (+ optional `E2E_USER_ID`); sent as
  `X-Tenant-Id` / `X-User-Id`.

## Run

```bash
# from customer360-api/, reuse the unit-test venv (.venv)
export E2E_BASE_URL="https://<uat-host>:8008"
export E2E_TENANT_ID="<tenant-uuid>"
export E2E_BEARER_TOKEN="<jwt>"        # SSO on; omit for SSO-off UAT
# export E2E_VERIFY_TLS=false          # if UAT uses a self-signed cert
# export E2E_TRACKING_BASE_URL="https://<uat-host>/data"  # for SCRUM-98

.venv/bin/python -m pytest tests/e2e -v          # Linux/macOS
# .venv/Scripts/python -m pytest tests/e2e -v    # Windows (Git Bash)
```

### Run all, or choose specific TEST_PLAN cases

`./test.sh` runs everything. To run specific `TEST_PLAN.md` case id(s), pass `CASES=`
(space-separated; a prefix selects the group):

```bash
CASES="S94-02 S94-07" ./test.sh     # just those two cases
CASES=S94 ./test.sh                 # all S94-* cases
CASES=R ./test.sh                   # all routing cases (needs E2E_ALLOW_DATA_WRITES=1)
./test.sh --case S93-05             # same, passed straight to pytest
./test.sh -k idempotent             # any pytest arg still works
```

Point at real data to see non-zero routing instead of the safe zero-match default:

```bash
export E2E_SEGMENT_ID="<existing-segment-uuid>"   # or:
export E2E_SEGMENT_SQL="lifecycle_stage = 'lead'"
.venv/bin/python -m pytest tests/e2e -v
```

## Test plan, routing coverage & cleanup

- **`TEST_PLAN.md`** maps every AC of SCRUM-93/94 to cases (positive / negative /
  boundary / idempotency / isolation / security / integrity / audit) and marks how
  each is covered (default E2E / opt-in routing / unit / DB smoke).
- **Default suite** (`test_crm_sync_e2e.py`, `test_schema_foundation_e2e.py`) uses the
  zero-match segment → **writes no `crm_*` rows**. Ephemeral segments/campaigns/leads/
  sources are auto-deleted by the `track` fixture; segment-delete cascades sync-runs.
- **Routing suite** (`test_routing_e2e.py`, `E2E_ALLOW_DATA_WRITES=1`) exercises Route
  A/B/C against one existing profile per stage and deletes exactly the rows it created
  (deterministic-PK, pre-existence-checked).
- **Email tracking suite** (`test_email_tracking_e2e.py`) targets customer360-event-api
  through `E2E_TRACKING_BASE_URL` and uses synthetic signed tokens only.
- **Sweeper** for a killed run:

  ```bash
  CLEANUP=1 ./test.sh      # or: ./test.sh --cleanup   (or: python cleanup.py)
  ```

  Deletes residual `e2e-` segments (cascading sync-runs) and `E2E ` campaigns for the tenant.

The repository-level `./run_all_tests.sh` runs hermetic unit suites only. It does
not call this live E2E harness; invoke `./test.sh` explicitly with UAT variables.

## Notes / safety

- **Idempotent + low-footprint.** Ephemeral segments/campaigns are deleted on
  teardown, and deleting a segment cascades to its `crm_segment_sync_runs` rows.
  With the default (impossible) predicate, the sync matches nothing and writes no
  `crm_*` rows — so it's safe to run repeatedly against UAT.
- A real predicate **will** upsert into `crm_lead` / `crm_contact` /
  `crm_customer_contacts` / `crm_transactions` / `crm_lead_source` for matched
  profiles (idempotent, but persistent). Use a disposable/known tenant on UAT.
- `sql_rules` must pass the WHERE-fragment safety validator (no `;`, no
  DML/DDL/SELECT keywords) — see `core/utils/sql_safety.py`.
