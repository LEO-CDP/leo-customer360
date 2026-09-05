# Customer 360 API and MCP Server

Production-oriented integration guide for frontend engineers, platform engineers, and QA/QC teams.

This document is aligned with:
- app.py
- core/apps/http_api_app.py
- core/apps/mcp_app.py
- core/routers/*.py
- core/mcptools/*.py

Last aligned: 2026-09-06

## 1. Service Overview

customer360-api provides two surfaces:
- REST API for Customer 360 business domains under /api/v1
- MCP server for AI-agent workflows under /mcp

Primary capabilities:
- Identity resolution and profile graph operations
- CRM, events, segmentation, reporting, content, and metadata APIs
- Tenant-isolated MCP tools backed by Redis API key mapping

## 2. Base Paths and Runtime

- Service root:
  - GET /
  - GET /health
- REST API prefix: /api/v1
- MCP prefix: /mcp
- OpenAPI docs: /docs
- root_path: /c360api (reverse-proxy deployments)

In proxied environments, APIs are typically served under /c360api as well.

## 3. Architecture and Ownership

- Main API app factory: core/apps/http_api_app.py
- MCP app factory: core/apps/mcp_app.py
- MCP auth and tenant context: core/mcptools/context.py
- MCP tool registry: core/mcptools/__init__.py
- One tool per file: core/mcptools/*.py

Design principles:
- Security isolation between REST and MCP surfaces
- Server-side tenant scoping only
- Tool-level modularity for safe future extension

## 4. Authentication and Tenant Context

### 4.1 REST API Authentication

All non-exempt API routes require:
- Authorization: Bearer <token>

Execution modes:
1. SSO mode (SSO_LOGIN=true)
   - Keycloak access token
   - Introspection + Redis token cache
2. Dev mode (SSO_LOGIN=false)
   - Local JWT from POST /api/v1/auth/login
   - Same bearer contract as SSO

Exempt API routes:
- /health
- /api/v1/metadata
- /api/v1/auth/login
- /api/v1/auth/callback
- /api/v1/auth/logout

Tenant context for DB RLS is applied as PostgreSQL session variables:
- app.tenant_id
- app.user_id

### 4.2 MCP Authentication

MCP requests require:
- X-API-Key: <api_key>

Redis mapping contract:
```bash
set apikey:{api_key} {tenant_id}
```

MCP flow:
- API key validated by core/auth.py
- tenant_id read from Redis value
- tenant_id bound in request context
- tools read tenant from server-side context (not from caller input)

## 5. Frontend Integration Guide

### 5.1 Dev Login Example

```bash
curl -s -X POST http://localhost:8008/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<DEFAULT_ROOT_PASSWORD>"}'
```

Use access_token as Bearer token for protected endpoints.

### 5.2 Required Headers

- REST API calls:
  - Authorization: Bearer <access_token>
- MCP calls:
  - X-API-Key: <api_key>

### 5.3 Pagination and Query Patterns

List endpoints commonly support:
- skip
- limit

Frontend recommendation:
- Default to limit + cursor/page state on client
- Handle empty pages and partial page counts gracefully

### 5.4 Error Handling Contract

Handle these status codes consistently in UI:
- 401 Unauthorized
- 403 Forbidden
- 422 Validation Error
- 500 Internal Server Error
- 503 Service Unavailable

Frontend recommendation:
- Map status code to user-safe message
- Preserve backend detail for diagnostic logs only

## 6. Platform Engineering Guide

### 6.1 Local Run

From customer360-api:
```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8008 --reload
```

### 6.2 Health Probes

- API health: GET /health
- MCP health: GET /mcp/health

### 6.3 Operational Dependencies

- PostgreSQL for persistent domain data
- Redis for:
  - token and identity caches
  - MCP API key to tenant mapping

### 6.4 Deployment Notes

- root_path /c360api must be respected by ingress/reverse proxy
- MCP is mounted as sub-app under /mcp with isolated auth dependency
- Verify Redis availability before enabling MCP clients

## 7. MCP Tooling Reference

Tool registry:
- core/mcptools/__init__.py

Current MCP tools:
- get_system_metrics
- search_data_sources_by_name

Tool files:
- core/mcptools/system_metrics_tool.py
- core/mcptools/search_data_sources_tool.py

### 7.1 Tool: get_system_metrics

Returns:
- current_date
- ram_usage_percent
- ram_used_gb
- ram_total_gb

### 7.2 Tool: search_data_sources_by_name

Inputs:
- keywords: str
- limit: int (default 5, max 20)

Behavior:
- tenant-scoped search using server-side tenant context
- keyword normalization + wildcard escaping for safer LIKE matching
- ranked by relevance and activity metrics

Returns only:
- id
- name
- total_tracked_event
- avg_daily_event
- avg_events_per_profile
- javascript_tags
- qr_code_data

## 8. Extending MCP Safely

To add a new tool:
1. Create one file per tool in core/mcptools/
2. Implement register_<tool>_tool(mcp: FastMCP)
3. Keep heavy logic in a service/helper class
4. Use tenant context helper from core/mcptools/context.py
5. Register in core/mcptools/__init__.py
6. Add contract and auth tests

## 9. API Domains and Endpoints

### 9.1 Core
- GET /
- GET /health

### 9.2 Metadata
- GET /api/v1/metadata/
- GET /api/v1/metadata/dagster
- GET /api/v1/metadata/domains
- GET /api/v1/metadata/data-sources
- GET /api/v1/metadata/data-sources/{data_source_id}
- POST /api/v1/metadata/data-sources
- PATCH /api/v1/metadata/data-sources/{data_source_id}
- DELETE /api/v1/metadata/data-sources/{data_source_id}
- GET /api/v1/metadata/scoring-models
- GET /api/v1/metadata/scoring-models/{scoring_model_name}
- POST /api/v1/metadata/scoring-models
- PATCH /api/v1/metadata/scoring-models/{scoring_model_name}
- DELETE /api/v1/metadata/scoring-models/{scoring_model_name}

### 9.3 CRM (Generic CRUD)
- /api/v1/campaigns
- /api/v1/campaign-members
- /api/v1/leads
- /api/v1/lead-sources
- /api/v1/contacts
- /api/v1/accounts
- /api/v1/opportunities
- /api/v1/industries

### 9.4 Campaign Analytics
- GET /api/v1/campaigns/analytics/summary
- GET /api/v1/campaigns/analytics
- GET /api/v1/campaigns/analytics/spend-trend
- GET /api/v1/campaigns/analytics/top

### 9.5 Identity Resolution
- master-profiles
- raw-profiles
- profile-links
- domain-profiles
- profile-attributes
- identity-index
- profile-merge-history
- persona and persona analytics
- resolution-status

### 9.6 Auth
- POST /api/v1/auth/login
- POST /api/v1/auth/callback
- POST /api/v1/auth/logout

### 9.7 Users
- GET /api/v1/users/me
- POST /api/v1/users
- GET /api/v1/users
- GET /api/v1/users/{user_id}
- PATCH /api/v1/users/{user_id}
- DELETE /api/v1/users/{user_id}
- GET /api/v1/users/{user_id}/sso-identities

### 9.8 Reporting
- GET /api/v1/reporting/summary
- GET /api/v1/reporting/master-profiles/duplicates
- GET /api/v1/reporting/identity-graph/coverage

### 9.9 Relations and Interaction Data
- /api/v1/relation-types
- /api/v1/relations
- /api/v1/customer-contacts
- /api/v1/transactions

### 9.10 Events
- GET /api/v1/events/
- GET /api/v1/events/{event_id}
- POST /api/v1/events/
- POST /api/v1/events/bulk

### 9.11 Content
- GET /api/v1/content-items/
- GET /api/v1/content-items/recommended
- GET /api/v1/content-items/count
- GET /api/v1/content-items/{content_item_id}
- POST /api/v1/content-items/
- PATCH /api/v1/content-items/{content_item_id}
- DELETE /api/v1/content-items/{content_item_id}

### 9.12 Graph
- GET /api/v1/graph-edges/
- GET /api/v1/graph-edges/count
- GET /api/v1/graph-edges/{edge_id}
- POST /api/v1/graph-edges/
- DELETE /api/v1/graph-edges/{edge_id}

### 9.13 Segmentation
- /api/v1/segments
- GET /api/v1/segments/{segment_id}/matched-profiles
- GET /api/v1/segments/{segment_id}/matched-profiles/count
- POST /api/v1/segments/{segment_id}/recompute
- POST /api/v1/segments/admin/defaults/seed
- POST /api/v1/segments/admin/recompute-all
- GET /api/v1/segments/admin/recompute-status/{run_id}
- GET /api/v1/segments/segmentable-profile-attributes

## 10. QA and QC Test Guide

### 10.1 Minimum Release Gate

1. Authentication flows
   - SSO bearer flow verified
   - Dev JWT flow verified
2. Tenant isolation
   - Cross-tenant read/write blocked
3. MCP security
   - Invalid key returns 401
   - Redis outage returns 503
   - Valid key enforces tenant-scoped data
4. Contract checks
   - Frontend-dependent payloads unchanged
   - MCP outputs include only approved fields
5. Regression
   - Unit tests pass

### 10.2 Test Commands

Run full service unit tests:
```bash
./run_unit_tests.sh
```

Run MCP-focused tests:
```bash
./.venv/bin/python -m pytest tests/test_mcp_app.py tests/test_mcp_auth.py -q
```

## 11. Route Shape Notes

- Some resources intentionally do not expose full CRUD.
- Some APIs are action-style or append-only by design.
- Keep client paths aligned with router-defined trailing slash behavior.

## 12. Source of Truth

Implementation references:
- customer360-api/app.py
- customer360-api/core/apps/http_api_app.py
- customer360-api/core/apps/mcp_app.py
- customer360-api/core/routers/*.py
- customer360-api/core/mcptools/*.py
