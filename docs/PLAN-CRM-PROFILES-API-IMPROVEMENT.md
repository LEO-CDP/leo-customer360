# CRM Profiles API Improvement Plan

Rules: 
- When data in `cdp_master_profiles` is ready, admin can trigger a data job to copy data into CRM tables.
- FOR CRM TABLES: `crm_lead`, `crm_contact`, `crm_account`, `crm_customer_contacts`

## 1) Why CRM Profiles APIs are missing currently

> **Correction (2026-07-28 review): the premise below was already out of date.**
> `crm_customer_contacts` already has a full model/schema/CRUD router — it just
> lives alongside the other "interaction" entities rather than in `crm.py`:
> - Model: `CustomerContact` in [customer360-api/core/models/relations.py](../customer360-api/core/models/relations.py)
> - Schemas: `CustomerContactCreate/Update/Read` in [customer360-api/core/schemas/relations.py](../customer360-api/core/schemas/relations.py)
> - Router: `customer_contacts_router` (built via `build_crud_router`) in [customer360-api/core/routers/relations_api.py](../customer360-api/core/routers/relations_api.py), prefix `/customer-contacts`, tag `"Customer Interactions"`
> - Already wired into `app.py` via `all_relations_routers`, and already used internally by `core/crud/profile360.py` (engagement summary / timeline endpoints).
>
> So **Phase 1 of this plan is already done** (see note in section 4). The only real remaining gap is the sync mechanism below.

- **Database tables exist, but routers for the CRM journey entities (`crm_lead`, `crm_contact`, `crm_account`, etc.) don't cover a sync-from-CDP flow**: The `crm_*` entities (Lead, Contact, Account, Opportunity, Industry, LeadSource, Campaign, CampaignMember) have CRUD routers via `_generic.py` (in `core/routers/crm_api.py`), but those are plain CRUD — nothing populates them from `cdp_master_profiles`.

- **No data sync mechanism**: Currently, CRM tables are manually populated or not populated at all. There is no automated or on-demand mechanism to copy **resolved master profile data** from `cdp_master_profiles` (the golden record after identity resolution) into operational CRM tables (`crm_lead`, `crm_contact`, `crm_account`).

- **No admin job trigger**: There is no endpoint for admins to request a profile sync job. No `core/routers/admin.py` exists yet. CRM tables remain stale unless manually updated via direct INSERT/UPDATE, defeating the purpose of identity resolution.

## 2) Target behavior 

**Admin-triggered data sync pipeline:**

1. Admin calls a new endpoint: `POST /api/v1/admin/crm/sync-profiles` with optional filters (e.g., domain, date range, lifecycle_stage).

2. Sync job:
   - Reads resolved `cdp_master_profiles` for the tenant (matching filters, status_code=1).
   - **Upserts** each profile into `crm_lead` or `crm_contact` based on `lifecycle_stage` (or domain/segmentation_tags).
   - Logs sync metadata (count, start/end time, status) for audit/admin visibility.
   - Returns job ID for polling progress (optional async support via Dagster).

3. Resulting tables are enriched with resolved identity:
   - `crm_lead` / `crm_contact` rows now have `first_name`, `last_name`, `email`, `phone` populated from the master profile's `first_name`, `last_name`, `email`, `phone_number` columns (note: `crm_lead`/`crm_contact` use `phone`, not `phone_number` — the sync must map the field name, not copy it verbatim).
   - Admin UI can then filter/segment leads/contacts using `cdp_master_profiles` AI/ML scores (engagement, CLV, churn risk, etc.) by joining back to the master profile (the CRM tables themselves don't gain new score columns).

4. Separate API for `crm_customer_contacts`:
   - CRUD endpoints (GET, POST, PATCH, DELETE) following the same pattern as other CRM entities.
   - Used by CS/success teams to log interaction events (call, email, support ticket, etc.) against a resolved customer.

## 3) Gaps between current and target

| Gap | Current State | Target State | Priority |
|-----|---------------|--------------|----------|
| ~~**CustomerContact router**~~ | ✅ Already implemented — model/schema/router exist in `relations.py`, wired into `app.py` | (done) | — |
| **Profile sync job** | No sync code; CRM tables manual or stale | Sync service to upsert profiles into `crm_lead`/`crm_contact` | High |
| **Admin trigger endpoint** | No `core/routers/admin.py`, no admin API surface | `POST /api/v1/admin/crm/sync-profiles` with optional filters + status polling | High |
| **Sync metadata tracking** | No audit log | New table `crm_profile_sync_job` to log job runs (start_time, end_time, status, tenant_id, filter_spec, profile_count) | Medium |
| **Tests for sync & CRUD** | No dedicated `customer_contacts` tests, no sync tests | Unit + integration tests for CustomerContact CRUD + sync job | High |

## 4) Implementation (recommended sequence)

### Phase 1: Add CustomerContact CRUD — ✅ ALREADY DONE, no work needed

This phase's stated goal already exists in the codebase, under slightly
different file names than assumed above:

1. **ORM model** — already defined as `CustomerContact` in
   [customer360-api/core/models/relations.py](../customer360-api/core/models/relations.py)
   (not `models/crm.py`), matching the real `crm_customer_contacts` DDL:
   `contact_id`, `tenant_id`, `user_id`, `master_profile_id` (FK to
   `cdp_master_profiles`), `contact_type`, `contact_channel`,
   `contact_content`, `contact_date` (server-generated `now()`).

2. **Pydantic schemas** — already defined as `CustomerContactCreate` /
   `CustomerContactUpdate` / `CustomerContactRead` in
   [customer360-api/core/schemas/relations.py](../customer360-api/core/schemas/relations.py)
   (not `schemas/crm.py`). Note `tenant_id` is a **required** field on
   create, and `contact_date` is server-generated (not settable on create).

3. **Router** — already built via `build_crud_router()` in
   [customer360-api/core/routers/relations_api.py](../customer360-api/core/routers/relations_api.py)
   (not `routers/crm_api.py`) as `customer_contacts_router`, prefix
   `/customer-contacts`, tag `"Customer Interactions"` (not
   `"CRM - Customer Interactions"`).

4. **Wired into FastAPI app** — already included via
   `all_relations_routers` in `customer360-api/app.py`.

5. **Unit tests — still missing.** This is the one real remaining task from
   this phase: add `customer360-api/tests/test_customer_contacts.py`
   (CRUD operations, tenant isolation/RLS), following the pattern in
   `tests/test_multi_tenant_isolation.py` / `tests/test_tenant_scoped_router.py`.

### Phase 2: Create Profile Sync Job (2–3 hours)
1. **Add metadata table** (`database-schema.sql`):
   ```sql
   CREATE TABLE customer360.crm_profile_sync_job (
       job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
       tenant_id UUID NOT NULL,
       started_at TIMESTAMP DEFAULT now(),
       completed_at TIMESTAMP,
       status TEXT, -- "running", "success", "failed"
       filter_spec JSONB, -- e.g., {"domain": "retail", "lifecycle_stage": "customer"}
       profile_count INT,
       error_message TEXT,
       created_by UUID REFERENCES customer360.sys_user(user_id)
   );
   ```

> **Correction:** the codebase is **synchronous** SQLAlchemy throughout (a
> pooled `Session` via `core/database.py:get_db`, not an `AsyncSession`/
> `get_db_session`), and there is **no** `Depends(get_current_user)` /
> `CurrentUser` dependency anywhere in the code. Auth is handled by the
> `auth_middleware` in `core/auth.py`, which stashes the caller's resolved
> `tenant_id`/`user_id` onto `request.state` and the raw Keycloak token
> payload onto `request.state.user`. Admin-role checks follow the pattern
> already used in `core/routers/segment_api.py`
> (`_is_platform_admin`/`_is_tenant_admin`, reading roles off
> `request.state.user`). The snippets below are corrected to match.

2. **Add sync service** (`customer360-api/core/crud/crm_sync.py`):
   ```python
   from sqlalchemy.orm import Session

   def sync_profiles_to_crm(
       db: Session,
       tenant_id: uuid.UUID,
       filters: Optional[dict] = None,  # optional: {"domain": "retail", "lifecycle_stage": "customer"}
       created_by_user_id: Optional[uuid.UUID] = None,
   ) -> dict:
       """
       Reads cdp_master_profiles (status_code=1) matching filters for the
       tenant; upserts into crm_lead (lifecycle_stage in prospect/lead) or
       crm_contact (lifecycle_stage in customer/vip/dormant/churn_risk),
       mapping master profile first_name/last_name/email/phone_number onto
       crm_lead/crm_contact's first_name/last_name/email/phone (note the
       phone -> phone_number name difference). Runs synchronously and
       returns once complete (no Dagster/async job queue exists yet in this
       repo -- see note below).
       Returns: {"job_id": "...", "profile_count": N, "status": "success"}
       """
   ```

3. **Add router endpoint** (`customer360-api/core/routers/admin.py`, new file):
   ```python
   from fastapi import APIRouter, Body, Depends, HTTPException, Request
   from sqlalchemy.orm import Session

   from core.database import get_db

   router = APIRouter(prefix="/admin", tags=["Admin"])

   @router.post("/crm/sync-profiles")
   def trigger_crm_profile_sync(
       request: Request,
       filters: Optional[dict] = Body(None),
       db: Session = Depends(get_db),
   ) -> dict:
       # Require admin role -- reuse the _is_tenant_admin/_is_platform_admin
       # helpers pattern from core/routers/segment_api.py against request.state.user.
       tenant_id = getattr(request.state, "tenant_id", None)
       user_id = getattr(request.state, "user_id", None)
       if tenant_id is None:
           raise HTTPException(status_code=400, detail="No tenant context found")
       job = sync_profiles_to_crm(db, tenant_id, filters, user_id)
       return {"job_id": job["job_id"], "status": "started"}
   ```

4. **Add polling endpoint** (optional, only meaningful once the job actually
   runs asynchronously -- e.g. offloaded to a `backend-system/*` Dagster job
   like the other pipelines, rather than run inline in the request):
   ```python
   @router.get("/crm/sync-profiles/{job_id}")
   def get_sync_job_status(job_id: uuid.UUID, db: Session = Depends(get_db)):
       # Return job status, profile_count, error_message
       ...
   ```

### Phase 3: Tests & Documentation (1 hour)
1. Add unit tests for `customer_contacts` CRUD (still missing, see Phase 1) + integration tests for sync job
2. Update `customer360-api.md` with new `/admin/crm/sync-profiles` endpoints (the existing `/customer-contacts` endpoint should already be documented there since it's already shipped — verify and fix if not)
3. Add docstrings to sync service

## 5) Suggested API request shapes

### List Customer Contacts
> Corrected: the generic CRUD router (`build_crud_router`) only supports
> `tenant_id`/`skip`/`limit` as query filters — `master_profile_id` and
> `contact_type` are **not** supported filters on the list endpoint today
> (they'd silently be ignored by FastAPI, not filtered on).
```
GET /api/v1/customer-contacts/?tenant_id=<uuid>&skip=0&limit=50
Response: [
  {
    "tenant_id": "<uuid>",
    "user_id": null,
    "master_profile_id": "<uuid>",
    "contact_type": "call",
    "contact_channel": "phone",
    "contact_content": "Discussed renewal options",
    "contact_id": "<uuid>",
    "contact_date": "2026-07-28T10:30:00Z"
  }
]
```

### Create Customer Contact
> Corrected: `tenant_id` is a required field on create (per
> `CustomerContactBase`), and `contact_date` is server-generated — it can't
> be set on create.
```
POST /api/v1/customer-contacts/
Body: {
  "tenant_id": "<uuid>",
  "master_profile_id": "<uuid>",
  "contact_type": "email",
  "contact_channel": "email",
  "contact_content": "Sent product demo link"
}
Response: { "contact_id": "<uuid>", "contact_date": "2026-07-28T09:00:00Z", ... (full object) }
```

### Trigger CRM Profile Sync (Admin)
```
POST /api/v1/admin/crm/sync-profiles
Body: {
  "filters": {
    "domain": "retail",
    "lifecycle_stage": "customer",
    "updated_after": "2026-07-01"
  }
}
Response: {
  "job_id": "<uuid>",
  "status": "started",
  "message": "Profile sync job queued. Poll /admin/crm/sync-profiles/<job_id> for progress."
}
```

### Poll Sync Job Status (Admin)
```
GET /api/v1/admin/crm/sync-profiles/<job_id>
Response: {
  "job_id": "<uuid>",
  "status": "success",
  "started_at": "2026-07-28T10:00:00Z",
  "completed_at": "2026-07-28T10:02:30Z",
  "profile_count": 1250,
  "error_message": null
}
```

## 6) Rollout strategy

**Phase 1 (Week 1):**
- CustomerContact CRUD API is already merged/deployed — only add the missing unit tests.
- Test locally with synthetic data.

**Phase 2 (Week 2):**
- Deploy sync job code to staging.
- Admins test sync endpoint with safe filters (e.g., `lifecycle_stage = "prospect"`).
- Verify upserts don't corrupt production CRM data.

**Phase 3 (Week 3+):**
- Enable sync in production after smoke tests pass.
- Announce to CS/Sales teams that they can now log `crm_customer_contacts` via API.
- Marketing/BI can query upserted `crm_lead`/`crm_contact` rows enriched with identity resolution scores.

## 7) Definition of done

- [x] `CustomerContact` model, schema, and CRUD router implemented and wired into app.py (already done, in `models/relations.py` / `schemas/relations.py` / `routers/relations_api.py`)
- [ ] All `crm_customer_contacts` CRUD operations pass unit + integration tests (tests still need to be written)
- [ ] Tenant isolation enforced (RLS, auth middleware) — verify existing RLS policy covers `crm_customer_contacts` since the table already exists in `database-schema.sql`.
- [ ] `crm_profile_sync_job` metadata table created in schema
- [ ] Sync job service (`crm_sync.py`) reads `cdp_master_profiles`, upserts into `crm_lead`/`crm_contact` by `lifecycle_stage` (or configurable field)
- [ ] Admin endpoint `POST /api/v1/admin/crm/sync-profiles` triggers sync (auth-gated to admin only)
- [ ] Polling endpoint `GET /api/v1/admin/crm/sync-profiles/{job_id}` returns status + profile_count
- [ ] Integration tests for sync job (success + error cases)
- [ ] API documentation (`customer360-api.md`) updated with new endpoints and examples
- [ ] Code review + team sign-off
- [ ] Deployed to staging & validated end-to-end
- [ ] Released to production
