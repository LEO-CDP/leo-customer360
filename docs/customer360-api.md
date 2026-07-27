# Customer 360 API

This document standardizes endpoint usage for the Customer 360 API.

- Base URL: `/api/v1`
- Interactive docs: `/docs`
- Health checks: `/` and `/health`

## Authentication and Tenant Context

- In production (`SSO_LOGIN=true`), send `Authorization: Bearer <token>`.
- Tenant isolation is enforced by Postgres RLS with `app.tenant_id` from request auth context.
- In local/dev (`SSO_LOGIN=false`), tenant context can be set with headers:
	- `X-Tenant-Id: <tenant-uuid>`
	- `X-User-Id: <user-id-or-uuid>`

## Common CRUD Pattern

Most entities follow this common pattern:

1. List items
- `GET /api/v1/<resource>/?skip=0&limit=50&tenant_id=<optional-tenant-uuid>`

2. Count items
- `GET /api/v1/<resource>/count?tenant_id=<optional-tenant-uuid>`

3. Get one item
- `GET /api/v1/<resource>/<id>`

4. Create item
- `POST /api/v1/<resource>/`

5. Update item
- `PATCH /api/v1/<resource>/<id>`

6. Delete item
- `DELETE /api/v1/<resource>/<id>`

## CRM APIs

These resources use the common CRUD pattern above:

- `/campaigns`
- `/campaign-members`
- `/leads`
- `/lead-sources`
- `/contacts`
- `/accounts`
- `/opportunities`
- `/industries`

### How To Use

1. List campaigns
- `GET /api/v1/campaigns/?skip=0&limit=50&tenant_id=<tenant-uuid>`

2. Create a lead
- `POST /api/v1/leads/`

3. Update an opportunity
- `PATCH /api/v1/opportunities/<opportunity-uuid>`

4. Delete a contact
- `DELETE /api/v1/contacts/<contact-uuid>`

## Identity Resolution APIs

### Master Profiles (`/master-profiles`)

1. List master profiles
- `GET /api/v1/master-profiles/?tenant_id=<tenant-uuid>&domain=retail&lifecycle_stage=customer&skip=0&limit=50`

2. Search master profiles by text (`q`)
- `GET /api/v1/master-profiles/?q=nguyen&tenant_id=<tenant-uuid>`

3. Count master profiles
- `GET /api/v1/master-profiles/count?tenant_id=<tenant-uuid>&domain=retail`

4. Get one master profile
- `GET /api/v1/master-profiles/<master-profile-uuid>`

5. Create/update/delete master profile
- `POST /api/v1/master-profiles/`
- `PATCH /api/v1/master-profiles/<master-profile-uuid>`
- `DELETE /api/v1/master-profiles/<master-profile-uuid>`

6. Profile 360 insight endpoints
- `GET /api/v1/master-profiles/<master-profile-uuid>/links`
- `GET /api/v1/master-profiles/<master-profile-uuid>/engagement-summary?days=90`
- `GET /api/v1/master-profiles/<master-profile-uuid>/channel-activity?days=90`
- `GET /api/v1/master-profiles/<master-profile-uuid>/top-interests?limit=5`
- `GET /api/v1/master-profiles/<master-profile-uuid>/timeline?limit=20`

### Raw Profiles (`/raw-profiles`)

1. List raw profiles
- `GET /api/v1/raw-profiles/?tenant_id=<tenant-uuid>&domain=retail&source_system=appsflyer&status_code=1&skip=0&limit=50`

2. Count raw profiles
- `GET /api/v1/raw-profiles/count?tenant_id=<tenant-uuid>&domain=retail&source_system=appsflyer&status_code=1`

3. Get/create/update/delete raw profile
- `GET /api/v1/raw-profiles/<raw-profile-uuid>`
- `POST /api/v1/raw-profiles/`
- `PATCH /api/v1/raw-profiles/<raw-profile-uuid>`
- `DELETE /api/v1/raw-profiles/<raw-profile-uuid>`

### Profile Links (`/profile-links`)

1. List profile links
- `GET /api/v1/profile-links/?tenant_id=<tenant-uuid>&raw_profile_id=<raw-profile-uuid>&master_profile_id=<master-profile-uuid>&skip=0&limit=50`

2. Get/create/delete profile link
- `GET /api/v1/profile-links/<link-uuid>`
- `POST /api/v1/profile-links/`
- `DELETE /api/v1/profile-links/<link-uuid>`

### Profile Attributes (`/profile-attributes`)

Uses the common CRUD pattern.

1. List attributes
- `GET /api/v1/profile-attributes/?skip=0&limit=50`

2. Count attributes
- `GET /api/v1/profile-attributes/count`

3. Get/create/update/delete attribute
- `GET /api/v1/profile-attributes/<attribute-id>`
- `POST /api/v1/profile-attributes/`
- `PATCH /api/v1/profile-attributes/<attribute-id>`
- `DELETE /api/v1/profile-attributes/<attribute-id>`

### Resolution Status (`/resolution-status`)

1. Get real-time identity-resolution worker status
- `GET /api/v1/resolution-status/`

## Reporting APIs

### How To Use

1. CIR summary
- `GET /api/v1/reporting/summary?tenant_id=<tenant-uuid>`

2. Duplicate master profiles
- `GET /api/v1/reporting/master-profiles/duplicates?tenant_id=<tenant-uuid>&skip=0&limit=50`

3. Identity graph coverage
- `GET /api/v1/reporting/identity-graph/coverage?tenant_id=<tenant-uuid>`

## Relations APIs

### Relation Types (`/relation-types`)

Uses the common CRUD pattern (global dictionary-style resource).

1. List relation types
- `GET /api/v1/relation-types/?skip=0&limit=50`

2. Create relation type
- `POST /api/v1/relation-types/`

### CDP Relations (`/relations`)

Uses the common CRUD pattern.

1. List relations
- `GET /api/v1/relations/?tenant_id=<tenant-uuid>&skip=0&limit=50`

2. Get one relation
- `GET /api/v1/relations/<relation-uuid>`

### Customer Interactions (`/customer-contacts`, `/transactions`)

Both use the common CRUD pattern.

1. List customer contacts
- `GET /api/v1/customer-contacts/?tenant_id=<tenant-uuid>&skip=0&limit=50`

2. List transactions
- `GET /api/v1/transactions/?tenant_id=<tenant-uuid>&skip=0&limit=50`

## Events APIs

High-volume stream endpoints with direct event ingestion support.

### How To Use

1. Create one event (auto-links to raw profile)
- `POST /api/v1/events/`
- If `raw_profile_id` is omitted, provide at least one identity hint (`email`, `phone_number`, `external_customer_id`, `device_id`, `advertising_id`, `cookie_id`, or `session_id`) and the API will find/create a `cdp_raw_profiles_stage` row before inserting `cdp_raw_events`.
- Optional idempotency: send `event_dedup_key`; re-sending the same key for the same `(tenant_id, source_system)` returns the existing event instead of creating a duplicate row.

2. Bulk create events (small/medium batches)
- `POST /api/v1/events/bulk`
- Body is an array of event payloads (same shape as `POST /api/v1/events/`).

3. List events with optional filters
- `GET /api/v1/events/?tenant_id=<tenant-uuid>&master_profile_id=<master-profile-uuid>&domain=retail&channel=web&event_category=engagement&event_name=page_view&event_time_from=2026-01-01T00:00:00&event_time_to=2026-01-31T23:59:59&skip=0&limit=50`

4. Get one event
- `GET /api/v1/events/<event-uuid>`

## Personalized Content APIs

### Content Items (`/content-items`)

Uses the common CRUD pattern with additional filters.

1. List content items
- `GET /api/v1/content-items/?tenant_id=<tenant-uuid>&domain=retail&item_type=news&skip=0&limit=50`

2. Count content items
- `GET /api/v1/content-items/count?tenant_id=<tenant-uuid>&domain=retail&item_type=news`

3. Get/create/update/delete content item
- `GET /api/v1/content-items/<content-item-uuid>`
- `POST /api/v1/content-items/`
- `PATCH /api/v1/content-items/<content-item-uuid>`
- `DELETE /api/v1/content-items/<content-item-uuid>`

4. Get recommended content for a profile
- `GET /api/v1/content-items/recommended?master_profile_id=<master-profile-uuid>&item_type=news&limit=8`

## Graph APIs

### Graph Edges (`/graph-edges`)

This resource has custom CRUD shape because of a composite key design in storage.

1. List edges
- `GET /api/v1/graph-edges/?relation=belongs_to&from_id=<node-id>&to_id=<node-id>&skip=0&limit=50`

2. Count edges
- `GET /api/v1/graph-edges/count`

3. Get/create/delete edge
- `GET /api/v1/graph-edges/<edge-id>`
- `POST /api/v1/graph-edges/`
- `DELETE /api/v1/graph-edges/<edge-id>`

## Segmentation APIs

### Segments (`/segments`)

Uses the common CRUD pattern.

1. List segments
- `GET /api/v1/segments/?tenant_id=<tenant-uuid>&skip=0&limit=50`

2. Count segments
- `GET /api/v1/segments/count?tenant_id=<tenant-uuid>`

3. Get/create/update/delete segment
- `GET /api/v1/segments/<segment-uuid>`
- `POST /api/v1/segments/`
- `PATCH /api/v1/segments/<segment-uuid>`
- `DELETE /api/v1/segments/<segment-uuid>`

4. Execute segment matching
- `GET /api/v1/segments/<segment-uuid>/matched-profiles?skip=0&limit=50`
- `GET /api/v1/segments/<segment-uuid>/matched-profiles/count`

### Admin Default Segment Seeding

1. Seed caller tenant defaults
- `POST /api/v1/segments/admin/defaults/seed`

2. Seed one tenant
- `POST /api/v1/segments/admin/defaults/seed?tenant_id=<tenant-uuid>`

3. Seed all tenants
- `POST /api/v1/segments/admin/defaults/seed?all_tenants=true`

Notes:
- `tenant_id` and `all_tenants=true` are mutually exclusive.
- With SSO enabled, role checks apply:
	- Caller tenant seed: tenant admin role required.
	- Other tenant seed: platform admin role required.
	- All tenants seed: platform admin role required.

## Operational Recommendation for SaaS Scale

For automation, use a two-step pattern in onboarding or migration jobs:

1. Provision tenant and baseline config.
2. Trigger seeded defaults explicitly via the admin endpoint:
	 - `POST /api/v1/segments/admin/defaults/seed?tenant_id=<tenant-uuid>`

This makes initialization observable, idempotent, and safe for retries in distributed deployments.

## API Quick-Reference (Frontend and QA)

Use this section for fast copy/paste during UI integration and test execution.

### Environment Template

```bash
BASE_URL="http://localhost:8000/api/v1"
TOKEN="<bearer-token>"
TENANT_ID="<tenant-uuid>"
MASTER_PROFILE_ID="<master-profile-uuid>"
SEGMENT_ID="<segment-uuid>"
```

### Header Template

```bash
-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json"
```

### CRM

| Module | Method | Endpoint | Copy/Paste Example |
|---|---|---|---|
| Campaigns | GET | `/campaigns/` | `curl -X GET "$BASE_URL/campaigns/?tenant_id=$TENANT_ID&skip=0&limit=50" -H "Authorization: Bearer $TOKEN"` |
| Leads | POST | `/leads/` | `curl -X POST "$BASE_URL/leads/" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '<lead-json>'` |
| Contacts | PATCH | `/contacts/{contact_id}` | `curl -X PATCH "$BASE_URL/contacts/<contact-uuid>" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '<patch-json>'` |
| Opportunities | DELETE | `/opportunities/{opportunity_id}` | `curl -X DELETE "$BASE_URL/opportunities/<opportunity-uuid>" -H "Authorization: Bearer $TOKEN"` |

### Identity Resolution

| Module | Method | Endpoint | Copy/Paste Example |
|---|---|---|---|
| Master Profiles List | GET | `/master-profiles/` | `curl -X GET "$BASE_URL/master-profiles/?tenant_id=$TENANT_ID&domain=retail&skip=0&limit=50" -H "Authorization: Bearer $TOKEN"` |
| Master Profiles Search | GET | `/master-profiles/?q=...` | `curl -X GET "$BASE_URL/master-profiles/?tenant_id=$TENANT_ID&q=nguyen" -H "Authorization: Bearer $TOKEN"` |
| Master Profile Timeline | GET | `/master-profiles/{id}/timeline` | `curl -X GET "$BASE_URL/master-profiles/$MASTER_PROFILE_ID/timeline?limit=20" -H "Authorization: Bearer $TOKEN"` |
| Raw Profiles List | GET | `/raw-profiles/` | `curl -X GET "$BASE_URL/raw-profiles/?tenant_id=$TENANT_ID&status_code=1&skip=0&limit=50" -H "Authorization: Bearer $TOKEN"` |
| Profile Links List | GET | `/profile-links/` | `curl -X GET "$BASE_URL/profile-links/?tenant_id=$TENANT_ID&master_profile_id=$MASTER_PROFILE_ID" -H "Authorization: Bearer $TOKEN"` |
| Resolution Status | GET | `/resolution-status/` | `curl -X GET "$BASE_URL/resolution-status/" -H "Authorization: Bearer $TOKEN"` |

### Reporting

| Module | Method | Endpoint | Copy/Paste Example |
|---|---|---|---|
| CIR Summary | GET | `/reporting/summary` | `curl -X GET "$BASE_URL/reporting/summary?tenant_id=$TENANT_ID" -H "Authorization: Bearer $TOKEN"` |
| Duplicates | GET | `/reporting/master-profiles/duplicates` | `curl -X GET "$BASE_URL/reporting/master-profiles/duplicates?tenant_id=$TENANT_ID&skip=0&limit=50" -H "Authorization: Bearer $TOKEN"` |
| Identity Graph Coverage | GET | `/reporting/identity-graph/coverage` | `curl -X GET "$BASE_URL/reporting/identity-graph/coverage?tenant_id=$TENANT_ID" -H "Authorization: Bearer $TOKEN"` |

### Relations and Interactions

| Module | Method | Endpoint | Copy/Paste Example |
|---|---|---|---|
| Relation Types | GET | `/relation-types/` | `curl -X GET "$BASE_URL/relation-types/?skip=0&limit=50" -H "Authorization: Bearer $TOKEN"` |
| Relations | GET | `/relations/` | `curl -X GET "$BASE_URL/relations/?tenant_id=$TENANT_ID&skip=0&limit=50" -H "Authorization: Bearer $TOKEN"` |
| Customer Contacts | GET | `/customer-contacts/` | `curl -X GET "$BASE_URL/customer-contacts/?tenant_id=$TENANT_ID&skip=0&limit=50" -H "Authorization: Bearer $TOKEN"` |
| Transactions | GET | `/transactions/` | `curl -X GET "$BASE_URL/transactions/?tenant_id=$TENANT_ID&skip=0&limit=50" -H "Authorization: Bearer $TOKEN"` |

### Events

| Module | Method | Endpoint | Copy/Paste Example |
|---|---|---|---|
| Events List | GET | `/events/` | `curl -X GET "$BASE_URL/events/?tenant_id=$TENANT_ID&master_profile_id=$MASTER_PROFILE_ID&skip=0&limit=50" -H "Authorization: Bearer $TOKEN"` |
| Event Detail | GET | `/events/{event_id}` | `curl -X GET "$BASE_URL/events/<event-uuid>" -H "Authorization: Bearer $TOKEN"` |

### Personalized Content

| Module | Method | Endpoint | Copy/Paste Example |
|---|---|---|---|
| Content Items List | GET | `/content-items/` | `curl -X GET "$BASE_URL/content-items/?tenant_id=$TENANT_ID&domain=retail&item_type=news&skip=0&limit=50" -H "Authorization: Bearer $TOKEN"` |
| Content Items Count | GET | `/content-items/count` | `curl -X GET "$BASE_URL/content-items/count?tenant_id=$TENANT_ID&domain=retail&item_type=news" -H "Authorization: Bearer $TOKEN"` |
| Recommended Content | GET | `/content-items/recommended` | `curl -X GET "$BASE_URL/content-items/recommended?master_profile_id=$MASTER_PROFILE_ID&item_type=news&limit=8" -H "Authorization: Bearer $TOKEN"` |

### Graph

| Module | Method | Endpoint | Copy/Paste Example |
|---|---|---|---|
| Graph Edges List | GET | `/graph-edges/` | `curl -X GET "$BASE_URL/graph-edges/?relation=belongs_to&skip=0&limit=50" -H "Authorization: Bearer $TOKEN"` |
| Graph Edges Count | GET | `/graph-edges/count` | `curl -X GET "$BASE_URL/graph-edges/count" -H "Authorization: Bearer $TOKEN"` |
| Graph Edge Detail | GET | `/graph-edges/{edge_id}` | `curl -X GET "$BASE_URL/graph-edges/<edge-id>" -H "Authorization: Bearer $TOKEN"` |

### Segmentation

| Module | Method | Endpoint | Copy/Paste Example |
|---|---|---|---|
| Segments List | GET | `/segments/` | `curl -X GET "$BASE_URL/segments/?tenant_id=$TENANT_ID&skip=0&limit=50" -H "Authorization: Bearer $TOKEN"` |
| Segment Match List | GET | `/segments/{id}/matched-profiles` | `curl -X GET "$BASE_URL/segments/$SEGMENT_ID/matched-profiles?skip=0&limit=50" -H "Authorization: Bearer $TOKEN"` |
| Segment Match Count | GET | `/segments/{id}/matched-profiles/count` | `curl -X GET "$BASE_URL/segments/$SEGMENT_ID/matched-profiles/count" -H "Authorization: Bearer $TOKEN"` |
| Seed Defaults (Caller Tenant) | POST | `/segments/admin/defaults/seed` | `curl -X POST "$BASE_URL/segments/admin/defaults/seed" -H "Authorization: Bearer $TOKEN"` |
| Seed Defaults (One Tenant) | POST | `/segments/admin/defaults/seed?tenant_id=...` | `curl -X POST "$BASE_URL/segments/admin/defaults/seed?tenant_id=$TENANT_ID" -H "Authorization: Bearer $TOKEN"` |
| Seed Defaults (All Tenants) | POST | `/segments/admin/defaults/seed?all_tenants=true` | `curl -X POST "$BASE_URL/segments/admin/defaults/seed?all_tenants=true" -H "Authorization: Bearer $TOKEN"` |

### Full Core Entity CRUD Matrix

This matrix is the complete endpoint coverage for core data entities.

| Entity | List | Get | Create | Update | Delete | Count | Notes |
|---|---|---|---|---|---|---|---|
| Campaigns | `GET /api/v1/campaigns/` | `GET /api/v1/campaigns/{id}` | `POST /api/v1/campaigns/` | `PATCH /api/v1/campaigns/{id}` | `DELETE /api/v1/campaigns/{id}` | `GET /api/v1/campaigns/count` | tenant filter supported |
| Campaign Members | `GET /api/v1/campaign-members/` | `GET /api/v1/campaign-members/{id}` | `POST /api/v1/campaign-members/` | `PATCH /api/v1/campaign-members/{id}` | `DELETE /api/v1/campaign-members/{id}` | `GET /api/v1/campaign-members/count` | tenant filter supported |
| Leads | `GET /api/v1/leads/` | `GET /api/v1/leads/{id}` | `POST /api/v1/leads/` | `PATCH /api/v1/leads/{id}` | `DELETE /api/v1/leads/{id}` | `GET /api/v1/leads/count` | tenant filter supported |
| Lead Sources | `GET /api/v1/lead-sources/` | `GET /api/v1/lead-sources/{id}` | `POST /api/v1/lead-sources/` | `PATCH /api/v1/lead-sources/{id}` | `DELETE /api/v1/lead-sources/{id}` | `GET /api/v1/lead-sources/count` | tenant filter supported |
| Contacts | `GET /api/v1/contacts/` | `GET /api/v1/contacts/{id}` | `POST /api/v1/contacts/` | `PATCH /api/v1/contacts/{id}` | `DELETE /api/v1/contacts/{id}` | `GET /api/v1/contacts/count` | tenant filter supported |
| Accounts | `GET /api/v1/accounts/` | `GET /api/v1/accounts/{id}` | `POST /api/v1/accounts/` | `PATCH /api/v1/accounts/{id}` | `DELETE /api/v1/accounts/{id}` | `GET /api/v1/accounts/count` | tenant filter supported |
| Opportunities | `GET /api/v1/opportunities/` | `GET /api/v1/opportunities/{id}` | `POST /api/v1/opportunities/` | `PATCH /api/v1/opportunities/{id}` | `DELETE /api/v1/opportunities/{id}` | `GET /api/v1/opportunities/count` | tenant filter supported |
| Industries | `GET /api/v1/industries/` | `GET /api/v1/industries/{id}` | `POST /api/v1/industries/` | `PATCH /api/v1/industries/{id}` | `DELETE /api/v1/industries/{id}` | `GET /api/v1/industries/count` | tenant filter supported |
| Master Profiles | `GET /api/v1/master-profiles/` | `GET /api/v1/master-profiles/{id}` | `POST /api/v1/master-profiles/` | `PATCH /api/v1/master-profiles/{id}` | `DELETE /api/v1/master-profiles/{id}` | `GET /api/v1/master-profiles/count` | supports `q`, domain, lifecycle_stage |
| Raw Profiles | `GET /api/v1/raw-profiles/` | `GET /api/v1/raw-profiles/{id}` | `POST /api/v1/raw-profiles/` | `PATCH /api/v1/raw-profiles/{id}` | `DELETE /api/v1/raw-profiles/{id}` | `GET /api/v1/raw-profiles/count` | supports source_system, status_code |
| Profile Links | `GET /api/v1/profile-links/` | `GET /api/v1/profile-links/{id}` | `POST /api/v1/profile-links/` | N/A | `DELETE /api/v1/profile-links/{id}` | N/A | list supports raw/master profile filters |
| Profile Attributes | `GET /api/v1/profile-attributes/` | `GET /api/v1/profile-attributes/{id}` | `POST /api/v1/profile-attributes/` | `PATCH /api/v1/profile-attributes/{id}` | `DELETE /api/v1/profile-attributes/{id}` | `GET /api/v1/profile-attributes/count` | matching-rule metadata |
| Relation Types | `GET /api/v1/relation-types/` | `GET /api/v1/relation-types/{id}` | `POST /api/v1/relation-types/` | `PATCH /api/v1/relation-types/{id}` | `DELETE /api/v1/relation-types/{id}` | `GET /api/v1/relation-types/count` | global dictionary entity |
| Relations | `GET /api/v1/relations/` | `GET /api/v1/relations/{id}` | `POST /api/v1/relations/` | `PATCH /api/v1/relations/{id}` | `DELETE /api/v1/relations/{id}` | `GET /api/v1/relations/count` | tenant filter supported |
| Customer Contacts | `GET /api/v1/customer-contacts/` | `GET /api/v1/customer-contacts/{id}` | `POST /api/v1/customer-contacts/` | `PATCH /api/v1/customer-contacts/{id}` | `DELETE /api/v1/customer-contacts/{id}` | `GET /api/v1/customer-contacts/count` | tenant filter supported |
| Transactions | `GET /api/v1/transactions/` | `GET /api/v1/transactions/{id}` | `POST /api/v1/transactions/` | `PATCH /api/v1/transactions/{id}` | `DELETE /api/v1/transactions/{id}` | `GET /api/v1/transactions/count` | tenant filter supported |
| Content Items | `GET /api/v1/content-items/` | `GET /api/v1/content-items/{id}` | `POST /api/v1/content-items/` | `PATCH /api/v1/content-items/{id}` | `DELETE /api/v1/content-items/{id}` | `GET /api/v1/content-items/count` | includes `/recommended` endpoint |
| Segments | `GET /api/v1/segments/` | `GET /api/v1/segments/{id}` | `POST /api/v1/segments/` | `PATCH /api/v1/segments/{id}` | `DELETE /api/v1/segments/{id}` | `GET /api/v1/segments/count` | includes matched-profiles endpoints |
| Graph Edges | `GET /api/v1/graph-edges/` | `GET /api/v1/graph-edges/{id}` | `POST /api/v1/graph-edges/` | N/A | `DELETE /api/v1/graph-edges/{id}` | `GET /api/v1/graph-edges/count` | custom router, no patch |
| Events | `GET /api/v1/events/` | `GET /api/v1/events/{id}` | `POST /api/v1/events/`, `POST /api/v1/events/bulk` | N/A | N/A | N/A | supports direct ingest + auto raw-profile linking + idempotency via `event_dedup_key` |
| Resolution Status | `GET /api/v1/resolution-status/` | N/A | N/A | N/A | N/A | N/A | read-only status singleton |

### QA Smoke Sequence (Minimal)

1. Check service health.
```bash
curl -X GET "http://localhost:8000/health"
```

2. Validate auth + tenant-scoped list endpoint.
```bash
curl -X GET "$BASE_URL/master-profiles/?tenant_id=$TENANT_ID&skip=0&limit=5" -H "Authorization: Bearer $TOKEN"
```

3. Validate reporting endpoint.
```bash
curl -X GET "$BASE_URL/reporting/summary?tenant_id=$TENANT_ID" -H "Authorization: Bearer $TOKEN"
```

4. Validate segmentation execution path.
```bash
curl -X GET "$BASE_URL/segments/$SEGMENT_ID/matched-profiles/count" -H "Authorization: Bearer $TOKEN"
```
