# LEO Customer 360 DAO

`leo-customer360-dao` is the shared Python package for Customer 360 database
access. It is the persistence boundary used by `customer360-api` and the
Dagster services under `backend-system`.

The package owns:

- SQLAlchemy models for the Customer 360 schema
- Pydantic schemas used by persistence-facing code
- Generic CRUD and domain-specific database operations
- Tenant-scoped repositories and aggregate queries
- PostgreSQL row-level-security session context
- S3 event-lake query support used by profile analytics
- SQL safety, date, password, and optional Redis-cache utilities

It does not own FastAPI routers, HTTP middleware, Dagster orchestration, AI
providers, SMTP health checks, or campaign-draft planning. Those remain in
`customer360-api` because they are application services rather than reusable
data access code.

## Requirements

- Python 3.10+
- PostgreSQL 16-compatible database for runtime use
- A database schema containing the `customer360` tables and extensions used by
  the models, including `pgvector` where applicable
- S3-compatible credentials only for event-lake queries
- Redis credentials only when DAO read-through caching is enabled

The package can be imported without opening a database connection. The engine
is created when `leo_customer360_dao.database` is imported; actual queries are
executed when a session is used.

## Installation

### From a published package index

```bash
python -m pip install leo-customer360-dao
```

The package is currently released from this repository through the tagged
GitHub Actions workflow. Configure the PyPI trusted publisher, publish a
versioned tag such as `dao-v0.1.0`, and then install it from the configured
package index.

### From this repository

```bash
python -m pip install ./customer360-dao
```

For editable development:

```bash
python -m pip install --editable ./customer360-dao
```

To install the checked-out DAO into every Customer 360 microservice virtual
environment:

```bash
bash customer360-dao/install-local.sh
```

This covers `customer360-api`, `customer360-event-api`, `ads-server`, the shared
`backend-system` environment, and each backend Dagster code location. Missing
`.venv` directories are created. The script installs the DAO with
`--no-deps --editable`, so it never tries to download the unpublished package
from PyPI.

On a fresh checkout, install each service's dependencies after registering the
local DAO:

```bash
bash customer360-dao/install-local.sh --requirements
```

To target one service while debugging:

```bash
bash customer360-dao/install-local.sh --service customer360-api
bash customer360-dao/install-local.sh --service backend-system/segmentation --requirements
```

The local installer must run before a service installs a requirements file that
contains the bare `leo-customer360-dao` requirement. Once the local editable
distribution is installed, pip treats that requirement as already satisfied.

The package uses a `src/` layout. Do not add `customer360-dao/src` manually to
`PYTHONPATH` in deployed services; install the distribution instead.

## Package Map

```text
customer360-dao/
  pyproject.toml
  README.md
  run_tests.sh
  src/
    leo_customer360_dao/
      __init__.py
      cache.py
      config.py
      database.py
      crud/
      models/
      repositories/
      schemas/
      utils/
    tests/
```

### Runtime modules

| Module | Responsibility |
| --- | --- |
| `config.py` | `Settings` and the module-level `settings` configuration object. Loads `.env` and environment variables. |
| `database.py` | SQLAlchemy engine, `SessionLocal`, tenant/user transaction context, and framework-neutral `get_db()`. |
| `cache.py` | Lazy, optional Redis client used by DAO read-through caches. Returns `None` when caching is disabled or unavailable. |
| `models/` | SQLAlchemy ORM model definitions and the shared `Base`. |
| `schemas/` | Pydantic request/read models shared by persistence-facing services. |
| `crud/` | Generic CRUD plus identity, segmentation, CRM-sync, profile, and email-provider operations. |
| `repositories/` | Reusable filtered, aggregate, S3, identity, CRM, graph, persona, relation, and user queries. |
| `utils/` | Date-window helpers, password/JWT helpers, and SQL injection-safety validators. |

## Models

Importing `leo_customer360_dao.models` imports every ORM model so
`Base.metadata` is fully populated for foreign-key and relationship
resolution.

### System models: `models.system`

- `SysUser` and `SysUserInfo`: tenant-scoped users and SSO/local identities
- `SysDomain` and `SysTenantDomain`: business-domain catalog and tenant enablement
- `SysDataSource`: ingestion and tracking-source metadata
- `SysAuditLog`: compliance and before/after change records
- `sys_tenant_table` and `sys_organization_table`: lightweight FK tables used
  for model metadata resolution

### Identity and scoring models: `models.identity`

- `CdpMasterProfile`: resolved golden customer record
- `CdpRawProfileStage`: inbound source-profile landing table
- `CdpDomainProfile`: domain-specific attributes attached to a master profile
- `CdpProfileLink`: raw-to-master identity links
- `CdpProfileAttribute`: segmentable and identity-resolution attribute catalog
- `CdpIdentityIndex`: normalized O(1) identity lookup index
- `CdpProfileMergeHistory`: profile merge audit history
- `CdpCustomerPersona`, `CdpPersonaArchetype`, `CdpPersonaFeature`,
  `CdpPersonaHistory`, `CdpPersonaScoreDetail`: persona data
- `CdpScoringModel`: scoring model metadata
- `CdpIdResolutionStatus`: identity-resolution runtime state

### CRM models: `models.crm`

- `Account`, `Contact`, `Lead`, `LeadSource`, `Opportunity`, `Industry`
- `Campaign`, `CampaignMember`, `CampaignContentItem`, `CampaignReview`
- `CampaignDispatchLog`, `MessageTemplate`, `ConnectorConfig`, `SuppressionList`
- `SegmentSyncRun`

### Other domain models

- `models.content.CdpContentItem`: tenant-scoped personalized content
- `models.graph.GraphEdge`: customer graph edges
- `models.relations.RelationType`, `CdpRelation`, `CustomerContact`,
  `Transaction`: relationship and interaction records
- `models.segmentation.CdpSegment`: segment definitions, rules, and membership
  metadata

The shared declarative base is available as:

```python
from leo_customer360_dao.models import Base
```

## Sessions, Tenancy, and RLS

`database.py` creates a pooled SQLAlchemy engine using the database settings.
The pool uses `pool_pre_ping`, configurable pool sizing, and connection
recycling.

`get_db()` is deliberately framework-neutral:

```python
from leo_customer360_dao.database import get_db

for db in get_db(tenant_id=tenant_id, user_id=user_id):
    # use db here
    db.commit()
```

`get_db()`:

1. Creates a `SessionLocal` session.
2. Stores `tenant_id` and `user_id` in `Session.info`.
3. Sets transaction-local PostgreSQL settings with `set_config(..., true)`.
4. Closes the session in a `finally` block.

An SQLAlchemy `after_begin` listener reapplies the same values whenever a new
transaction starts. This matters because commits, refreshes, and later ORM
queries can create new transactions on a pooled connection.

The API adapts request state to this function in its own FastAPI database
adapter. Dagster workers can pass job context directly.

**Tenant rule:** every service using this package must pass or establish the
correct tenant context. Repositories also apply explicit tenant filters where
appropriate as defense in depth; PostgreSQL RLS remains the database-level
boundary.

## CRUD Layer

### Generic CRUD: `crud.base.CRUDBase`

`CRUDBase[ModelType]` provides:

- `get(db, pk)`
- `list(db, skip=0, limit=100, **filters)`
- `count(db, **filters)`
- `create(db, obj_in)`
- `update(db, db_obj, obj_in)`
- `delete(db, db_obj)`

It validates dynamic filter and sort fields against SQLAlchemy mapper columns,
supports `ASC`/`DESC` sorting, and updates an `updated_at` column when present.

```python
from leo_customer360_dao.crud.base import CRUDBase
from leo_customer360_dao.models import CdpSegment

segments = CRUDBase(CdpSegment)
items = segments.list(db, tenant_id=tenant_id, status_code=1, limit=50)
```

### Domain CRUD modules

| Module | Important code |
| --- | --- |
| `crud.identity` | Identity/profile aggregate filters, pagination, and profile status queries. |
| `crud.segmentation` | `DOMAIN_ATTRIBUTES_JOIN_SQL`, safe segment membership recomputation, tag updates, and member counts. |
| `crud.crm_sync` | Segment-to-CRM routing, deterministic UUID5 IDs, idempotent upserts, and sync auditing. |
| `crud.profile360` | Engagement, channel activity, interests, and timeline aggregation across PostgreSQL and the S3 event lake. |
| `crud.email_provider` | Active email-provider lookup and tenant-safe provider upsert/deactivation. |

`crud.crm_sync` deliberately never fabricates customer transactions. It only
materializes transaction facts present in profile attributes.

## Repository Layer

Repositories accept a SQLAlchemy `Session` and keep query logic out of
routers and Dagster definitions.

| Module | Responsibility |
| --- | --- |
| `repositories.auth_repository` | Keycloak user lookup, login metadata refresh, and new user/SSO provisioning. |
| `repositories.campaign_repository` | Campaign performance and reporting queries. |
| `repositories.content_repository` | Tenant/domain content CRUD and recommended-content queries. |
| `repositories.crm_repository` | CRM entity repositories for accounts, contacts, leads, opportunities, and campaigns. |
| `repositories.event_query_repository` | Tenant-scoped S3/MinIO event discovery, decompression, Polars parsing, filtering, and volume summaries. |
| `repositories.master_profile_event_repository` | JSON event projections for resolved master profiles, including seven-day/range/source filtering. |
| `repositories.graph_repository` | Customer graph-edge queries. |
| `repositories.identity_repository` | Identity profile CRUD and identity-specific operations. |
| `repositories.persona_repository` | Persona archetype, feature, score, history, and customer-persona queries. |
| `repositories.relations_repository` | Relation types, customer contacts, transactions, and graph relations. |
| `repositories.reporting_respository` | CIR reporting aggregates and duplicate/coverage/persona summaries. The filename spelling is retained for compatibility. |
| `repositories.segment_respository` | Segment lookup, matched-profile queries, counts, attribute catalogs, and membership operations. The filename spelling is retained for compatibility. |
| `repositories.user_repository` | Tenant-scoped user/SSO CRUD and optional Redis profile caching. |

The `metadata_repository.py` and `campaign_draft_repository.py` modules remain
in `customer360-api` because they combine database operations with API health
probes, Dagster configuration, AI planning, and approval orchestration.

## Schemas

`schemas/` contains Pydantic models grouped by domain:

- `auth.py`: login and development-token payloads
- `content.py`: content item create/read/update models
- `crm.py`: CRM entities, campaigns, templates, provider configuration, and
  segment-sync payloads
- `event_query.py`: S3 event query and volume response models
- `graph.py`: graph-edge payloads
- `identity.py`: master/raw/domain profile, identity index, persona, and
  attribute-catalog payloads
- `profile360.py`: engagement, channel, interest, and timeline responses
- `relations.py`: relation, contact, and transaction payloads
- `reporting.py`: CIR summary and coverage responses
- `segmentation.py`: segment payloads and SQL-rule validation models
- `system.py`: system metadata and datasource payloads
- `user.py`: user, SSO identity, and user-list payloads

These schemas are transport-neutral and can be used by HTTP, Dagster, or other
service adapters without importing FastAPI.

## Utilities and Caching

### SQL safety: `utils.sql_safety`

Use `validate_sql_where_fragment()` before executing a user/configured segment
WHERE fragment and `validate_readonly_sql_statement()` for read-only SQL
statements. The validators reject statement stacking, comments, and DDL/DML
keywords. Validation is defense in depth; bind all values separately.

### Dates: `utils.datetime`

Provides shared UTC date-window and cutoff helpers used by identity/reporting
queries.

### Passwords and local tokens: `utils.security`

Provides PBKDF2 password hashing/verification and development JWT helpers.
Production SSO token validation remains an application concern.

### Redis: `cache.py`

`get_redis_client()` lazily creates a client from `Settings`. Redis is an
optimization, not a source of truth: unavailable or disabled Redis returns
`None`, allowing repositories to fall back to PostgreSQL.

## Configuration

`Settings` reads `.env` plus environment variables. Important groups include:

| Group | Main fields |
| --- | --- |
| Database | `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_SCHEMA` |
| Pooling | `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_RECYCLE_SECONDS`, `DB_POOL_PRE_PING`, `DB_ECHO_SQL` |
| Pagination | `API_DEFAULT_PAGE_SIZE`, `API_MAX_PAGE_SIZE` |
| S3 event lake | `EVENT_QUERY_MAX_DAYS`, `EVENT_S3_BUCKET`, `EVENT_RAW_PREFIX`, `S3_*`, `ANALYTICS_S3_ENDPOINT_URL` |
| Redis | `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`, `CACHE_ENABLED`, `CACHE_TTL_SECONDS` |
| Dagster | `DAGSTER_GRAPHQL_HOST`, `DAGSTER_GRAPHQL_PORT`, plus per-service job/location/repository names |
| Email health | `EMAIL_DISPATCH_ADAPTER`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS` |
| Local auth | `SSO_LOGIN`, `DEFAULT_ROOT_USERNAME`, `DEFAULT_ROOT_PASSWORD`, `DEV_JWT_SECRET`, `DEV_JWT_EXPIRES_MINUTES` |
| AI configuration | `CRM_EMAIL_AI_PROVIDER`, `OPENAI_*`, `GEMINI_*` |

The database URL is built from the database fields as a PostgreSQL psycopg2
URL. Do not commit secrets; use the repository `.env` or deployment secret
injection.

## Testing

DAO-owned tests live under `customer360-dao/src/tests`. They cover models,
CRUD, query construction, filtering, tenant scoping, SQL safety, repositories,
identity operations, segment operations, event queries, user repositories, and
cache behavior.

Run them with the package runner:

```bash
bash customer360-dao/run_tests.sh
```

Or from an environment that already has the dependencies:

```bash
python -m pytest -q customer360-dao/src/tests
```

The repository-wide runner executes the DAO suite and then the service suites:

```bash
./run_all_tests.sh
```

FastAPI router, middleware, MCP, Dagster, AI, SMTP, and application bootstrap
tests remain under `customer360-api/tests` because they exercise API-owned
behavior rather than reusable DAO code.

## Building and Publishing

Build a wheel locally:

```bash
python -m pip install --upgrade build
python -m build customer360-dao
```

Inspect the artifact before publishing:

```bash
unzip -l customer360-dao/dist/leo_customer360_dao-*.whl
```

The package discovery configuration includes only `leo_customer360_dao*`; the
source test suite is intentionally not bundled into the wheel.

Publishing is handled by `.github/workflows/publish-customer360-dao.yml` using
PyPI trusted publishing. The workflow listens for tags matching `dao-v*` and
publishes the contents of `customer360-dao/dist/`.

## Import Guidance

Use the installed package namespace directly:

```python
from leo_customer360_dao.config import settings
from leo_customer360_dao.database import get_db
from leo_customer360_dao.models import CdpMasterProfile
from leo_customer360_dao.repositories.identity_repository import IdentityRepository
```

Do not import `customer360-api/core/models`, `core.schemas`, or other removed
compatibility paths. The API core now contains application adapters and HTTP
orchestration; reusable persistence code belongs under `leo_customer360_dao`.
