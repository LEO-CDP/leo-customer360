# Customer 360 API Reference (Code-Aligned)

This document reflects the current implementation in:
- app.py
- core/routers/*.py

Last aligned: 2026-08-09.

## Base Paths

- Service root (app-level):
  - GET /
  - GET /health
- API base prefix for routers: /api/v1
- FastAPI OpenAPI UI: /docs
- App root_path is configured as /c360api for reverse-proxy deployments.
  - In proxied environments, routes are typically served under /c360api as well.

## Authentication and Tenant Context

- With SSO_LOGIN=true, bearer auth is enforced by middleware for non-exempt routes.
- Exempt paths currently include:
  - /health
  - /api/v1/metadata
  - /api/v1/metadata/dagster
  - /api/v1/metadata/domains
  - /api/v1/metadata/data-sources
- With SSO_LOGIN=false (local/dev), tenant context can be passed via headers:
  - X-Tenant-Id
  - X-User-Id

## Generated CRUD Pattern

The following resources are implemented with core/routers/_generic.py and expose:
- GET /<resource>/
- GET /<resource>/count
- GET /<resource>/{item_id}
- POST /<resource>/
- PATCH /<resource>/{item_id}
- DELETE /<resource>/{item_id}

Note:
- If the model has tenant_id, list/count support optional tenant_id filter.
- For list endpoints, pagination is skip + limit.

## Endpoint Catalog

### Core

- GET /
- GET /health

### Metadata

- GET /api/v1/metadata/
- GET /api/v1/metadata/dagster
- GET /api/v1/metadata/domains
- GET /api/v1/metadata/data-sources
- GET /api/v1/metadata/data-sources/{data_source_id}
- POST /api/v1/metadata/data-sources
- PATCH /api/v1/metadata/data-sources/{data_source_id}
- DELETE /api/v1/metadata/data-sources/{data_source_id}

### CRM (Generic CRUD Resources)

- /api/v1/campaigns
- /api/v1/campaign-members
- /api/v1/leads
- /api/v1/lead-sources
- /api/v1/contacts
- /api/v1/accounts
- /api/v1/opportunities
- /api/v1/industries

All resources above use the generated CRUD pattern.

### Campaign Analytics

- GET /api/v1/campaigns/analytics/summary
- GET /api/v1/campaigns/analytics
- GET /api/v1/campaigns/analytics/spend-trend
- GET /api/v1/campaigns/analytics/top

### Identity Resolution

Master Profiles:
- GET /api/v1/master-profiles/
- GET /api/v1/master-profiles/count
- GET /api/v1/master-profiles/{master_profile_id}
- GET /api/v1/master-profiles/{master_profile_id}/links
- GET /api/v1/master-profiles/{master_profile_id}/domain-profiles
- POST /api/v1/master-profiles/{master_profile_id}/domain-attributes
- GET /api/v1/master-profiles/{master_profile_id}/linked-raw-profiles/{raw_profile_id}
- GET /api/v1/master-profiles/{master_profile_id}/persona
- GET /api/v1/master-profiles/{master_profile_id}/persona-history
- GET /api/v1/master-profiles/{master_profile_id}/engagement-summary
- GET /api/v1/master-profiles/{master_profile_id}/channel-activity
- GET /api/v1/master-profiles/{master_profile_id}/top-interests
- GET /api/v1/master-profiles/{master_profile_id}/timeline
- POST /api/v1/master-profiles/
- PATCH /api/v1/master-profiles/{master_profile_id}
- DELETE /api/v1/master-profiles/{master_profile_id}

Raw Profiles:
- GET /api/v1/raw-profiles/
- GET /api/v1/raw-profiles/count
- GET /api/v1/raw-profiles/{raw_profile_id}
- POST /api/v1/raw-profiles/
- PATCH /api/v1/raw-profiles/{raw_profile_id}
- DELETE /api/v1/raw-profiles/{raw_profile_id}

Profile Links:
- GET /api/v1/profile-links/
- GET /api/v1/profile-links/{link_id}
- POST /api/v1/profile-links/
- DELETE /api/v1/profile-links/{link_id}

Domain Profiles (Generic CRUD Resource):
- /api/v1/domain-profiles

Profile Attributes (Generic CRUD Resource):
- /api/v1/profile-attributes

Identity Index (Generic CRUD Resource):
- /api/v1/identity-index

Profile Merge History:
- GET /api/v1/profile-merge-history/
- GET /api/v1/profile-merge-history/{merge_id}
- POST /api/v1/profile-merge-history/

Customer Personas:
- GET /api/v1/persona/list
- GET /api/v1/persona/analytics/summary
- GET /api/v1/persona/{persona_id}
- GET /api/v1/persona/{persona_id}/features
- GET /api/v1/persona/{persona_id}/score-details
- POST /api/v1/persona/
- PATCH /api/v1/persona/{persona_id}
- DELETE /api/v1/persona/{persona_id}

Persona Features:
- GET /api/v1/persona/features/
- GET /api/v1/persona/features/{feature_id}
- POST /api/v1/persona/features/

Persona Score Details:
- GET /api/v1/persona/score-details/
- GET /api/v1/persona/score-details/{score_id}
- POST /api/v1/persona/score-details/

Persona History:
- GET /api/v1/persona/history/
- GET /api/v1/persona/history/{history_id}
- POST /api/v1/persona/history/

Resolution Status:
- GET /api/v1/resolution-status/

### Users

- GET /api/v1/users/me
- POST /api/v1/users
- GET /api/v1/users
- GET /api/v1/users/{user_id}
- PATCH /api/v1/users/{user_id}
- DELETE /api/v1/users/{user_id} (`?hard_delete=true` for hard delete; default is soft delete/deactivate)
- GET /api/v1/users/{user_id}/sso-identities

Notes:
- `sys_user` (profile) and `sys_userinfo` (per-provider SSO identity) are decoupled; `UserResponse` embeds `sso_identities`.
- Username/email are immutable via PATCH (only settable at creation); `UserUpdate` intentionally excludes them.
- `GET /api/v1/users/me` and `GET /api/v1/users/{user_id}` are Redis read-through cached (`user:profile:{tenant_id}:{user_id}`, 120s TTL) since the profile is resolved on nearly every authenticated request; every write (`PATCH`/`DELETE`/SSO link/unlink) invalidates the entry.

### Reporting

- GET /api/v1/reporting/summary
- GET /api/v1/reporting/master-profiles/duplicates
- GET /api/v1/reporting/identity-graph/coverage

### Relations and Customer Interactions

Relation Types (Generic CRUD Resource):
- /api/v1/relation-types

Relations (Generic CRUD Resource):
- /api/v1/relations

Customer Contacts (Generic CRUD Resource):
- /api/v1/customer-contacts

Transactions (Generic CRUD Resource):
- /api/v1/transactions

### Events

- GET /api/v1/events/
- GET /api/v1/events/{event_id}
- POST /api/v1/events/
- POST /api/v1/events/bulk

### Content

- GET /api/v1/content-items/
- GET /api/v1/content-items/recommended
- GET /api/v1/content-items/count
- GET /api/v1/content-items/{content_item_id}
- POST /api/v1/content-items/
- PATCH /api/v1/content-items/{content_item_id}
- DELETE /api/v1/content-items/{content_item_id}

### Graph

- GET /api/v1/graph-edges/
- GET /api/v1/graph-edges/count
- GET /api/v1/graph-edges/{edge_id}
- POST /api/v1/graph-edges/
- DELETE /api/v1/graph-edges/{edge_id}

### Segmentation

Segments (Generic CRUD Resource):
- /api/v1/segments

Additional Segmentation Endpoints:
- GET /api/v1/segments/{segment_id}/matched-profiles
- GET /api/v1/segments/{segment_id}/matched-profiles/count
- POST /api/v1/segments/{segment_id}/recompute
- POST /api/v1/segments/admin/defaults/seed
- POST /api/v1/segments/admin/recompute-all
- GET /api/v1/segments/admin/recompute-status/{run_id}
- GET /api/v1/segments/segmentable-profile-attributes

## Notes on Route Shape

- Some resources intentionally do not expose full CRUD:
  - profile-links: no PATCH, no /count
  - profile-merge-history: append/read only
  - persona/features, persona/score-details, persona/history: read/create only
  - graph-edges: no PATCH
  - events: custom ingest/list/detail APIs
  - users: no /count; DELETE defaults to soft delete (status=INACTIVE), hard delete is opt-in via query param
- Trailing slash behavior follows router definitions. Keep client paths aligned with the list above.

## Verification Source

This file was updated from the latest route declarations in:
- customer360-api/app.py
- customer360-api/core/routers/crm.py
- customer360-api/core/routers/relations.py
- customer360-api/core/routers/identity.py
- customer360-api/core/routers/segment.py
- customer360-api/core/routers/events.py
- customer360-api/core/routers/content.py
- customer360-api/core/routers/graph.py
- customer360-api/core/routers/reporting.py
- customer360-api/core/routers/metadata.py
- customer360-api/core/routers/user_api.py

## Endpoint Matrix (Machine-Readable)

Auth expectation values:
- public: no bearer token expected
- bearer_if_sso: bearer token expected when SSO_LOGIN=true
- tenant_admin_if_sso: tenant-admin or platform-admin role expected when SSO_LOGIN=true
- platform_admin_if_sso: platform-admin role expected when SSO_LOGIN=true

Format: CSV with columns resource,method,auth_expectation.

```csv
resource,method,auth_expectation
/,GET,public
/health,GET,public
/api/v1/metadata/,GET,public
/api/v1/metadata/dagster,GET,public
/api/v1/metadata/domains,GET,public
/api/v1/metadata/data-sources,GET,public
/api/v1/metadata/data-sources,POST,bearer_if_sso
/api/v1/metadata/data-sources/{data_source_id},GET,bearer_if_sso
/api/v1/metadata/data-sources/{data_source_id},PATCH,bearer_if_sso
/api/v1/metadata/data-sources/{data_source_id},DELETE,bearer_if_sso
/api/v1/campaigns/,GET,bearer_if_sso
/api/v1/campaigns/count,GET,bearer_if_sso
/api/v1/campaigns/{item_id},GET,bearer_if_sso
/api/v1/campaigns/,POST,bearer_if_sso
/api/v1/campaigns/{item_id},PATCH,bearer_if_sso
/api/v1/campaigns/{item_id},DELETE,bearer_if_sso
/api/v1/campaign-members/,GET,bearer_if_sso
/api/v1/campaign-members/count,GET,bearer_if_sso
/api/v1/campaign-members/{item_id},GET,bearer_if_sso
/api/v1/campaign-members/,POST,bearer_if_sso
/api/v1/campaign-members/{item_id},PATCH,bearer_if_sso
/api/v1/campaign-members/{item_id},DELETE,bearer_if_sso
/api/v1/leads/,GET,bearer_if_sso
/api/v1/leads/count,GET,bearer_if_sso
/api/v1/leads/{item_id},GET,bearer_if_sso
/api/v1/leads/,POST,bearer_if_sso
/api/v1/leads/{item_id},PATCH,bearer_if_sso
/api/v1/leads/{item_id},DELETE,bearer_if_sso
/api/v1/lead-sources/,GET,bearer_if_sso
/api/v1/lead-sources/count,GET,bearer_if_sso
/api/v1/lead-sources/{item_id},GET,bearer_if_sso
/api/v1/lead-sources/,POST,bearer_if_sso
/api/v1/lead-sources/{item_id},PATCH,bearer_if_sso
/api/v1/lead-sources/{item_id},DELETE,bearer_if_sso
/api/v1/contacts/,GET,bearer_if_sso
/api/v1/contacts/count,GET,bearer_if_sso
/api/v1/contacts/{item_id},GET,bearer_if_sso
/api/v1/contacts/,POST,bearer_if_sso
/api/v1/contacts/{item_id},PATCH,bearer_if_sso
/api/v1/contacts/{item_id},DELETE,bearer_if_sso
/api/v1/accounts/,GET,bearer_if_sso
/api/v1/accounts/count,GET,bearer_if_sso
/api/v1/accounts/{item_id},GET,bearer_if_sso
/api/v1/accounts/,POST,bearer_if_sso
/api/v1/accounts/{item_id},PATCH,bearer_if_sso
/api/v1/accounts/{item_id},DELETE,bearer_if_sso
/api/v1/opportunities/,GET,bearer_if_sso
/api/v1/opportunities/count,GET,bearer_if_sso
/api/v1/opportunities/{item_id},GET,bearer_if_sso
/api/v1/opportunities/,POST,bearer_if_sso
/api/v1/opportunities/{item_id},PATCH,bearer_if_sso
/api/v1/opportunities/{item_id},DELETE,bearer_if_sso
/api/v1/industries/,GET,bearer_if_sso
/api/v1/industries/count,GET,bearer_if_sso
/api/v1/industries/{item_id},GET,bearer_if_sso
/api/v1/industries/,POST,bearer_if_sso
/api/v1/industries/{item_id},PATCH,bearer_if_sso
/api/v1/industries/{item_id},DELETE,bearer_if_sso
/api/v1/campaigns/analytics/summary,GET,bearer_if_sso
/api/v1/campaigns/analytics,GET,bearer_if_sso
/api/v1/campaigns/analytics/spend-trend,GET,bearer_if_sso
/api/v1/campaigns/analytics/top,GET,bearer_if_sso
/api/v1/master-profiles/,GET,bearer_if_sso
/api/v1/master-profiles/count,GET,bearer_if_sso
/api/v1/master-profiles/{master_profile_id},GET,bearer_if_sso
/api/v1/master-profiles/{master_profile_id}/links,GET,bearer_if_sso
/api/v1/master-profiles/{master_profile_id}/domain-profiles,GET,bearer_if_sso
/api/v1/master-profiles/{master_profile_id}/domain-attributes,POST,bearer_if_sso
/api/v1/master-profiles/{master_profile_id}/linked-raw-profiles/{raw_profile_id},GET,bearer_if_sso
/api/v1/master-profiles/{master_profile_id}/persona,GET,bearer_if_sso
/api/v1/master-profiles/{master_profile_id}/persona-history,GET,bearer_if_sso
/api/v1/master-profiles/{master_profile_id}/engagement-summary,GET,bearer_if_sso
/api/v1/master-profiles/{master_profile_id}/channel-activity,GET,bearer_if_sso
/api/v1/master-profiles/{master_profile_id}/top-interests,GET,bearer_if_sso
/api/v1/master-profiles/{master_profile_id}/timeline,GET,bearer_if_sso
/api/v1/master-profiles/,POST,bearer_if_sso
/api/v1/master-profiles/{master_profile_id},PATCH,bearer_if_sso
/api/v1/master-profiles/{master_profile_id},DELETE,bearer_if_sso
/api/v1/raw-profiles/,GET,bearer_if_sso
/api/v1/raw-profiles/count,GET,bearer_if_sso
/api/v1/raw-profiles/{raw_profile_id},GET,bearer_if_sso
/api/v1/raw-profiles/,POST,bearer_if_sso
/api/v1/raw-profiles/{raw_profile_id},PATCH,bearer_if_sso
/api/v1/raw-profiles/{raw_profile_id},DELETE,bearer_if_sso
/api/v1/profile-links/,GET,bearer_if_sso
/api/v1/profile-links/{link_id},GET,bearer_if_sso
/api/v1/profile-links/,POST,bearer_if_sso
/api/v1/profile-links/{link_id},DELETE,bearer_if_sso
/api/v1/domain-profiles/,GET,bearer_if_sso
/api/v1/domain-profiles/count,GET,bearer_if_sso
/api/v1/domain-profiles/{item_id},GET,bearer_if_sso
/api/v1/domain-profiles/,POST,bearer_if_sso
/api/v1/domain-profiles/{item_id},PATCH,bearer_if_sso
/api/v1/domain-profiles/{item_id},DELETE,bearer_if_sso
/api/v1/profile-attributes/,GET,bearer_if_sso
/api/v1/profile-attributes/count,GET,bearer_if_sso
/api/v1/profile-attributes/{item_id},GET,bearer_if_sso
/api/v1/profile-attributes/,POST,bearer_if_sso
/api/v1/profile-attributes/{item_id},PATCH,bearer_if_sso
/api/v1/profile-attributes/{item_id},DELETE,bearer_if_sso
/api/v1/identity-index/,GET,bearer_if_sso
/api/v1/identity-index/count,GET,bearer_if_sso
/api/v1/identity-index/{item_id},GET,bearer_if_sso
/api/v1/identity-index/,POST,bearer_if_sso
/api/v1/identity-index/{item_id},PATCH,bearer_if_sso
/api/v1/identity-index/{item_id},DELETE,bearer_if_sso
/api/v1/profile-merge-history/,GET,bearer_if_sso
/api/v1/profile-merge-history/{merge_id},GET,bearer_if_sso
/api/v1/profile-merge-history/,POST,bearer_if_sso
/api/v1/persona/list,GET,bearer_if_sso
/api/v1/persona/analytics/summary,GET,bearer_if_sso
/api/v1/persona/{persona_id},GET,bearer_if_sso
/api/v1/persona/{persona_id}/features,GET,bearer_if_sso
/api/v1/persona/{persona_id}/score-details,GET,bearer_if_sso
/api/v1/persona/,POST,bearer_if_sso
/api/v1/persona/{persona_id},PATCH,bearer_if_sso
/api/v1/persona/{persona_id},DELETE,bearer_if_sso
/api/v1/persona/features/,GET,bearer_if_sso
/api/v1/persona/features/{feature_id},GET,bearer_if_sso
/api/v1/persona/features/,POST,bearer_if_sso
/api/v1/persona/score-details/,GET,bearer_if_sso
/api/v1/persona/score-details/{score_id},GET,bearer_if_sso
/api/v1/persona/score-details/,POST,bearer_if_sso
/api/v1/persona/history/,GET,bearer_if_sso
/api/v1/persona/history/{history_id},GET,bearer_if_sso
/api/v1/persona/history/,POST,bearer_if_sso
/api/v1/resolution-status/,GET,bearer_if_sso
/api/v1/reporting/summary,GET,bearer_if_sso
/api/v1/reporting/master-profiles/duplicates,GET,bearer_if_sso
/api/v1/reporting/identity-graph/coverage,GET,bearer_if_sso
/api/v1/relation-types/,GET,bearer_if_sso
/api/v1/relation-types/count,GET,bearer_if_sso
/api/v1/relation-types/{item_id},GET,bearer_if_sso
/api/v1/relation-types/,POST,bearer_if_sso
/api/v1/relation-types/{item_id},PATCH,bearer_if_sso
/api/v1/relation-types/{item_id},DELETE,bearer_if_sso
/api/v1/relations/,GET,bearer_if_sso
/api/v1/relations/count,GET,bearer_if_sso
/api/v1/relations/{item_id},GET,bearer_if_sso
/api/v1/relations/,POST,bearer_if_sso
/api/v1/relations/{item_id},PATCH,bearer_if_sso
/api/v1/relations/{item_id},DELETE,bearer_if_sso
/api/v1/customer-contacts/,GET,bearer_if_sso
/api/v1/customer-contacts/count,GET,bearer_if_sso
/api/v1/customer-contacts/{item_id},GET,bearer_if_sso
/api/v1/customer-contacts/,POST,bearer_if_sso
/api/v1/customer-contacts/{item_id},PATCH,bearer_if_sso
/api/v1/customer-contacts/{item_id},DELETE,bearer_if_sso
/api/v1/transactions/,GET,bearer_if_sso
/api/v1/transactions/count,GET,bearer_if_sso
/api/v1/transactions/{item_id},GET,bearer_if_sso
/api/v1/transactions/,POST,bearer_if_sso
/api/v1/transactions/{item_id},PATCH,bearer_if_sso
/api/v1/transactions/{item_id},DELETE,bearer_if_sso
/api/v1/events/,GET,bearer_if_sso
/api/v1/events/{event_id},GET,bearer_if_sso
/api/v1/events/,POST,bearer_if_sso
/api/v1/events/bulk,POST,bearer_if_sso
/api/v1/content-items/,GET,bearer_if_sso
/api/v1/content-items/recommended,GET,bearer_if_sso
/api/v1/content-items/count,GET,bearer_if_sso
/api/v1/content-items/{content_item_id},GET,bearer_if_sso
/api/v1/content-items/,POST,bearer_if_sso
/api/v1/content-items/{content_item_id},PATCH,bearer_if_sso
/api/v1/content-items/{content_item_id},DELETE,bearer_if_sso
/api/v1/graph-edges/,GET,bearer_if_sso
/api/v1/graph-edges/count,GET,bearer_if_sso
/api/v1/graph-edges/{edge_id},GET,bearer_if_sso
/api/v1/graph-edges/,POST,bearer_if_sso
/api/v1/graph-edges/{edge_id},DELETE,bearer_if_sso
/api/v1/segments/,GET,bearer_if_sso
/api/v1/segments/count,GET,bearer_if_sso
/api/v1/segments/{item_id},GET,bearer_if_sso
/api/v1/segments/,POST,bearer_if_sso
/api/v1/segments/{item_id},PATCH,bearer_if_sso
/api/v1/segments/{item_id},DELETE,bearer_if_sso
/api/v1/segments/{segment_id}/matched-profiles,GET,bearer_if_sso
/api/v1/segments/{segment_id}/matched-profiles/count,GET,bearer_if_sso
/api/v1/segments/{segment_id}/recompute,POST,bearer_if_sso
/api/v1/segments/admin/defaults/seed,POST,tenant_admin_if_sso
/api/v1/segments/admin/recompute-all,POST,tenant_admin_if_sso
/api/v1/segments/admin/recompute-status/{run_id},GET,bearer_if_sso
/api/v1/segments/segmentable-profile-attributes,GET,bearer_if_sso
/api/v1/users/me,GET,bearer_if_sso
/api/v1/users,POST,bearer_if_sso
/api/v1/users,GET,bearer_if_sso
/api/v1/users/{user_id},GET,bearer_if_sso
/api/v1/users/{user_id},PATCH,bearer_if_sso
/api/v1/users/{user_id},DELETE,bearer_if_sso
/api/v1/users/{user_id}/sso-identities,GET,bearer_if_sso
```
