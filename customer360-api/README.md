# Customer 360 API and MCP Server

Integration guide for frontend engineers, platform engineers, and QA/QC teams.
The route inventory and behavior below reflect the current application wiring in
`app.py`, `core/apps/`, `core/routers/`, and `core/mcptools/`.

## 1. Service Overview

`customer360-api` exposes:

- A FastAPI REST API under `/api/v1` for Customer 360 data, identity resolution,
   CRM, segmentation, reporting, content, campaigns, and administration.
- A FastMCP server mounted under `/mcp` for authenticated AI-agent workflows.

The REST API uses PostgreSQL for domain data, Redis for caching/auth support,
Dagster for asynchronous long-running jobs, and S3-compatible storage for
behavioral-event queries and master-profile event projections.

## 2. Base Paths and Runtime

- Service root: `GET /` and `GET /health`
- REST prefix: `/api/v1`
- MCP mount: `/mcp`
- REST OpenAPI UI: `/docs`
- REST OpenAPI schema: `/openapi.json`
- Application `root_path`: `/c360api`
- Default local port: `8008`

When deployed behind a reverse proxy, include `/c360api` in the externally
visible URL as configured by the ingress.

### Behavioral event queries

These endpoints are read-only and query the S3/MinIO event lake. They return
aggregates, not raw event rows:

- `GET /api/v1/events/`: complete UTC-day event totals.
- `GET /api/v1/events/device-types`: totals grouped by normalized device type.

Both endpoints accept `days`, `event_time_from`, and optional filters. The daily
totals endpoint also accepts `master_profile_id`, `domain`, `channel`,
`event_category`, `event_name`, and `data_source_id`. The device-type endpoint
accepts `data_source_id`. `days` must be at least 1 and cannot exceed
`EVENT_QUERY_MAX_DAYS` (default `180`).

The repository resolves active, tenant-owned data sources from PostgreSQL and
reads immutable event objects from S3/MinIO. It does not write event data to
PostgreSQL. By default, an unset `EVENT_S3_BUCKET` selects the per-source
`data-tracking-{data_source_id}` bucket; set it to use a shared bucket layout.

### Master-profile timeline

`GET /api/v1/master-profiles/{master_profile_id}/timeline` reads the profile's
JSON event projection from `MASTER_PROFILE_S3_BUCKET` (default
`c360-master-profiles`) at `{master_profile_id}.json`. It returns a
most-recent-first unified feed of behavioral events, CRM transactions, and
customer-service contacts.

Query parameters:

- `limit`: 1-100, default `20`.
- `data_source_id`: optional active source owned by the profile's tenant.
- `from_event_time` and `to_event_time`: optional, order-independent bounds.

With no bounds, the endpoint uses the most recent seven days. Entries expose
source lineage, event category/name, device/domain metadata, page URL/title and
referrer, and a bounded scalar-only `event_data` object. It does not return the
complete raw event payload or identity object.

## 3. Architecture and Ownership

- App entrypoint: `app.py`
- REST app factory and router registration: `core/apps/http_api_app.py`
- MCP app factory: `core/apps/mcp_app.py`
- REST authentication middleware: `core/auth.py`
- MCP authentication and tenant context: `core/mcptools/context.py`
- MCP tool registry: `core/mcptools/__init__.py`
- One MCP tool per module: `core/mcptools/*.py`
- Shared application settings: `customer360-dao/src/leo_customer360_dao/config.py`

Business logic belongs in repositories/services. Routers provide transport,
validation, authorization, and response mapping. Tenant scope is resolved on
the server and reinforced by tenant-scoped queries and PostgreSQL RLS; clients
must not use caller-controlled tenant identifiers to access another tenant.

## 4. Authentication and Tenant Context

### 4.1 REST authentication

Protected REST requests use:

```http
Authorization: Bearer <access_token>
```

`SSO_LOGIN` selects the authentication mode:

1. `SSO_LOGIN=true`: the bearer token is introspected with Keycloak and the
    result is cached in Redis. Tenant/user claims are resolved and, when needed,
    the Keycloak identity is provisioned in `sys_user`.
2. `SSO_LOGIN=false`: `POST /api/v1/auth/login` issues a locally signed HS256
    JWT. The token uses the same bearer contract as SSO. Configure
    `DEV_JWT_SECRET` and `DEV_JWT_EXPIRES_MINUTES` for local development.

The public REST paths are:

- `GET /health`
- `GET /api/v1/metadata/` (the bare metadata route)
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/callback`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/zalo-redirect`

Nested metadata routes, including `/metadata/dagster`, `/metadata/domains`, and
`/metadata/smtp`, remain protected. Failed authentication and failed dev-login
attempts are Redis-rate-limited.

For database work, the resolved tenant context is pinned to the transaction
before tenant-scoped reads/writes. SSO admin actions require the appropriate
admin role; local mode intentionally relaxes those role checks for development.

### 4.2 MCP authentication

MCP requests use:

```http
X-API-Key: <api_key>
```

Redis maps the key to a tenant:

```bash
redis-cli SET 'apikey:<api_key>' '<tenant_id>'
```

The MCP dependency validates the key, binds the mapped tenant to request
context, and tools read that server-side context rather than accepting a
tenant id from the caller. Invalid keys return `401`; an unavailable Redis
authentication backend returns `503`.

## 5. Frontend Integration

### 5.1 Local login

```bash
curl -s -X POST http://localhost:8008/api/v1/auth/login \
   -H 'Content-Type: application/json' \
   -d '{"username":"admin","password":"<DEFAULT_ROOT_PASSWORD>"}'
```

Send the returned `access_token` as a bearer token on protected REST requests.
In SSO mode, use `POST /api/v1/auth/callback` to exchange the Keycloak
authorization code for tokens. `POST /api/v1/auth/logout` returns a Keycloak
end-session URL in SSO mode and a no-op response in dev mode.

### 5.2 Pagination and errors

Resource lists generally use `skip`/`limit`; profile and campaign analytics
lists may use `page`/`page_size`. Common limits are controlled by
`API_DEFAULT_PAGE_SIZE`/`API_MAX_PAGE_SIZE` or by endpoint-specific bounds.

Clients should handle at least `401`, `403`, `404`, `409`, `422`, `429`, `500`,
and `503`. Show user-safe messages in the UI and retain backend `detail` values
for diagnostic logs.

## 6. Local Run and Operations

From `customer360-api`:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8008 --reload
```

Health probes:

- `GET /health`: verifies the pooled SQLAlchemy connection with `SELECT 1` and
   reports build metadata and SSO mode.
- `GET /mcp/health`: MCP sub-application liveness response.

Runtime dependencies:

- PostgreSQL for persistent domain data and RLS.
- Redis for response caching, auth/token and identity caches, rate limiting, and
   MCP API-key-to-tenant mappings.
- Keycloak when `SSO_LOGIN=true`.
- Dagster for segmentation, identity, analytics, CRM-sync, and campaign jobs.
- S3/MinIO for event-lake and master-profile event projections.

### Important configuration

The settings object is defined in the local `customer360-dao` checkout. Common
API settings include:

- Database: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`,
   `DB_SCHEMA`.
- Auth: `SSO_LOGIN`, `SSO_LOGIN_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_CLIENT_ID`,
   `KEYCLOAK_CLIENT_SECRET`, `KEYCLOAK_CALLBACK_URL`, `KEYCLOAK_VERIFY_SSL`,
   `DEFAULT_ROOT_USERNAME`, `DEFAULT_ROOT_PASSWORD`, `DEV_JWT_SECRET`, and
   `DEV_JWT_EXPIRES_MINUTES`.
- Redis/cache: `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`,
   `CACHE_ENABLED`, and `CACHE_TTL_SECONDS`.
- Event lake: `EVENT_QUERY_MAX_DAYS`, `EVENT_S3_BUCKET`, `EVENT_RAW_PREFIX`,
   `S3_ENDPOINT_URL` or `ANALYTICS_S3_ENDPOINT_URL`, `S3_REGION`,
   `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_SESSION_TOKEN`,
   `S3_FORCE_PATH_STYLE`, and `S3_VERIFY_SSL`.
- Master-profile projections: `MASTER_PROFILE_S3_BUCKET`.
- Dagster: `DAGSTER_GRAPHQL_HOST` and `DAGSTER_GRAPHQL_PORT`, plus the
   per-service job/location/repository overrides in the settings class.
- Email health: `EMAIL_DISPATCH_ADAPTER` and the `SMTP_*` settings.

## 7. MCP Tooling

MCP endpoints and documentation are provided by FastMCP under `/mcp`; the exact
transport routes are library-version dependent. Current registered tools are:

### `get_system_metrics`

Read-only host summary returning `current_date`, `ram_usage_percent`,
`ram_used_gb`, and `ram_total_gb`.

### `search_data_sources_by_name`

Inputs:

- `keywords: str`
- `limit: int`, default `5`, clamped to `1-20`

The tool tokenizes and normalizes keywords, escapes SQL wildcard characters,
searches only the authenticated tenant's data sources, and ranks matches by
relevance and activity. Each result contains only `id`, `name`,
`total_tracked_event`, `avg_daily_event`, `avg_events_per_profile`,
`javascript_tags`, and `qr_code_data`.

To add a tool, create a module in `core/mcptools/`, implement
`register_<tool>_tool(mcp: FastMCP)`, use `get_bound_tenant_id()` for tenant
scope, register it in `core/mcptools/__init__.py`, and add auth/contract tests.

## 8. REST Route Inventory

All paths below are relative to `/api/v1`. Generic CRUD resources expose
`GET /`, `GET /count`, `GET /{id}`, `POST /`, `PATCH /{id}`, and
`DELETE /{id}` unless noted otherwise. Several list/create routes also accept
the no-trailing-slash form, which may be hidden from OpenAPI to avoid duplicate
operations.

### Health and metadata

- `GET /` and `GET /health`
- `GET /metadata/`
- `GET /metadata/dagster`
- `GET /metadata/smtp`
- `GET /metadata/domains`
- CRUD: `/data-sources`
- CRUD: `/ai-agents` (item key is `agent_code`; also has `/count`)

### Authentication and users

- `POST /auth/login`
- `POST /auth/callback`
- `POST /auth/logout`
- `GET /auth/zalo-redirect`
- `GET /users/me`
- `POST /users`
- `GET /users`
- `GET /users/{user_id}`
- `PATCH /users/{user_id}`
- `DELETE /users/{user_id}`
- `GET /users/{user_id}/sso-identities`

### CRM and campaign analytics

- Generic CRUD: `/campaigns`, `/campaign-members`, `/leads`, `/lead-sources`,
   `/contacts`, `/accounts`, `/opportunities`, `/industries`
- `GET /campaigns/analytics`
- `GET /campaigns/analytics/summary`
- `GET /campaigns/analytics/spend-trend`
- `GET /campaigns/analytics/top`

Campaign draft workflow under `/campaigns`:

- `POST /campaigns/draft`
- `POST /campaigns/zalo-draft`
- `GET /campaigns/{campaign_id}/content-items`
- `PATCH /campaigns/{campaign_id}/draft`
- `POST /campaigns/{campaign_id}/approve`
- `POST /campaigns/{campaign_id}/reject`
- `GET /campaigns/{campaign_id}/history`

Campaign activation and email administration under `/admin`:

- `POST /admin/campaigns/{campaign_id}/activate`
- `GET /admin/campaigns/{campaign_id}/dispatch-logs`
- `GET /admin/email-provider-config`
- `PUT /admin/email-provider-config`

### Identity resolution and personas

- Master profiles: `/master-profiles` with list/count/item CRUD plus
   `/{id}/links`, `/{id}/domain-profiles`, `/{id}/domain-attributes`,
   `/{id}/linked-raw-profiles/{raw_profile_id}`, `/{id}/persona`,
   `/{id}/persona-history`, `/{id}/engagement-summary`, `/{id}/channel-activity`,
   `/{id}/top-interests`, and `/{id}/timeline`.
- Generic CRUD: `/raw-profiles`, `/profile-links`, `/domain-profiles`,
   `/profile-attributes`, and `/identity-index`.
- Merge history: `GET` list/item and `POST /profile-merge-history/`.
- `GET /resolution-status/`.
- Persona archetypes: `/persona/archetypes` with list/item operations and
   `/{id}/master-profiles`.
- Customer personas: `GET /persona/list`, `GET /persona/analytics/summary`,
   `GET /persona/category/{persona_category}/master-profiles`, item reads and
   nested feature/score/master-profile reads, plus item CRUD.
- Persona explainability/history resources: `/persona/features`,
   `/persona/score-details`, and `/persona/history` provide list/item reads and
   append-only `POST` operations.

### Reporting, relations, and events

- `GET /reporting/summary`
- `GET /reporting/master-profiles/duplicates`
- `GET /reporting/identity-graph/coverage`
- CRUD: `/relation-types`, `/relations`, `/customer-contacts`, `/transactions`
- `GET /events/`
- `GET /events/device-types`

### Content and graph

- Content CRUD: `/content-items`
- `GET /content-items/recommended`
- `GET /content-items/count`
- Graph edges: `GET /graph-edges/`, `GET /graph-edges/count`,
   `GET /graph-edges/{edge_id}`, `POST /graph-edges/`,
   `DELETE /graph-edges/{edge_id}`

### Segmentation and asynchronous processing

- Generic CRUD: `/segments`
- `GET /segments/{segment_id}/matched-profiles`
- `GET /segments/{segment_id}/matched-profiles/count`
- `POST /segments/{segment_id}/recompute`
- `POST /segments/admin/defaults/seed`
- `POST /segments/admin/recompute-all`
- `GET /segments/admin/recompute-status/{run_id}`
- `GET /segments/segmentable-profile-attributes`
- `POST /admin/crm/sync-segment/{segment_id}` with optional `dry_run=true`
- `GET /admin/crm/sync-runs`
- `GET /admin/crm/sync-runs/{sync_run_id}`
- `GET /analytics/source-analytics/status`
- `POST /analytics/source-analytics/process`
- `GET /analytics/source-analytics/status/{run_id}`

### Zalo OA administration

- `PUT /admin/zalo/connector-config`
- `GET /admin/zalo/oa-config`
- `GET /admin/zalo/oauth-url`
- `POST /admin/zalo/templates/sync`
- `GET /admin/zalo/templates`
- `GET /admin/zalo/templates/{template_id}`

Zalo credentials are tenant-scoped. The OAuth consent URL is generated by the
authenticated admin route; the browser callback is the public
`GET /auth/zalo-redirect` route and is bound to the tenant by its signed state.

## 9. QA and QC Test Guide

### Minimum release gate

1. Verify dev JWT and Keycloak bearer flows.
2. Verify cross-tenant reads and writes are blocked.
3. Verify MCP invalid-key (`401`), Redis-unavailable (`503`), and valid-key
    tenant-scoping behavior.
4. Verify frontend payloads, route shapes, and approved MCP fields.
5. Run the unit suite.

Run all service unit tests from `customer360-api`:

```bash
./run_unit_tests.sh
```

Run MCP-focused tests:

```bash
./.venv/bin/python -m pytest tests/test_mcp_app.py tests/test_mcp_auth.py -q
```

The unit runner installs the sibling `customer360-dao` and `customer360-agent`
checkouts, installs `requirements.txt`, sets `SSO_LOGIN=true`, and excludes
live deployment tests under `tests/e2e`.

## 10. Source of Truth

Implementation references:

- `customer360-api/app.py`
- `customer360-api/core/apps/http_api_app.py`
- `customer360-api/core/apps/mcp_app.py`
- `customer360-api/core/auth.py`
- `customer360-api/core/routers/*.py`
- `customer360-api/core/mcptools/*.py`
- `customer360-dao/src/leo_customer360_dao/config.py`
