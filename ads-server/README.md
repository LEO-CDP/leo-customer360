# LEO Ad Server API

High-performance, multi-tenant ad serving API built on **FastAPI + SQLAlchemy + PostgreSQL 16**.

Designed for:
- **Low-latency ad serving** with Redis caching and indexed queries
- **Multi-tenant isolation** with strict `tenant_id` filtering on all operations
- **Production-grade architecture** with clean separation of concerns (repositories, services, controllers)
- **Scalability** supporting 40M+ profiles, multiple ad sources (local, Google Ads, affiliate networks), flexible formats
- **Flexible content** supporting display ads, native, video, carousel, and custom provider payloads

## Overview

The Ad Server exposes:
1. **Health endpoints** for monitoring service and database availability
2. **Ad query endpoints** for retrieving ads by ID or serving candidates for a placement
3. **Placement endpoints** for inventory configuration lookups
4. **JavaScript widget** (`ads.loader.js`) for client-side ad rendering with tracking

**Key characteristics:**
- All data scoped by `tenant_id` (multi-tenant isolation)
- Composable ad structure: Ad = Campaign + Creative + Placement
- Extensible JSONB payloads for provider-specific configuration
- Ready for Redis caching on hot paths (placement lookups, candidate ads)
- Comprehensive test coverage (unit, integration, repository)

---

## Quick Start

### Prerequisites
- Python 3.9+
- PostgreSQL 16+
- Redis (optional, for caching; API functions without it)
- Docker Compose (recommended for local dev)

### Installation

```bash
cd ads-server

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy .env template and edit
cp .env.example .env
# Edit .env:
#   LEO_AD_API_HOST=localhost
#   LEO_AD_API_PORT=9009
#   LEO_AD_DB_HOST=localhost
#   LEO_AD_DB_PORT=5432
#   LEO_AD_DB_USER=postgres
#   LEO_AD_DB_PASSWORD=your_password
#   LEO_AD_DB_NAME=customer360
```

### Running the Server

**Basic startup:**
```bash
./start.sh
```

**With database initialization + demo data:**
```bash
./start.sh --seed-demo-ads-server
```

**Stop/restart:**
```bash
./stop.sh      # Shutdown gracefully
./restart.sh   # Stop and start
```

### Development Mode

```bash
# With auto-reload on file changes
UVICORN_RELOAD=true ./start.sh

# Or run directly with uvicorn
uvicorn app:app --reload --port 9009
```

### API Documentation

- **Swagger UI**: http://localhost:9009/docs
- **ReDoc**: http://localhost:9009/redoc
- **Root info**: GET http://localhost:9009/

---

## API Endpoints

### Health & Monitoring

**GET** `/`
Service information and links.

```json
{
  "service": "leo-ad-server-api",
  "status": "ok",
  "version": "1.0.0",
  "schema": "leo_ads",
  "docs": "/docs"
}
```

**GET** `/health`
Lightweight health check (no database access).

```json
{
  "status": "ok",
  "service": "leo-ad-server-api"
}
```

**GET** `/health/database`
Verify PostgreSQL connectivity.

```json
{
  "status": "ok",
  "database": "reachable",
  "schema": "leo_ads"
}
```

### Ads

**GET** `/ads/{ad_id}`
Retrieve a single ad by its primary key.

**Parameters:**
- `ad_id` (int, path): Internal ad ID

**Response:**
```json
{
  "ad_id": 12345,
  "tenant_id": 1,
  "ad_key": "ad_1001",
  "campaign_id": 100,
  "creative_id": 500,
  "placement_id": 10,
  "status": "active",
  "score_weight": 1.5,
  "frequency_cap": 3,
  "metadata": {},
  "created_at": "2024-01-01T10:00:00Z",
  "updated_at": "2024-01-15T14:30:00Z"
}
```

**Note:** Use `ad_key` for external/user-facing references, not `ad_id`.

### Placements

**GET** `/placements/{placement_key}`
Retrieve an active placement by its external key.

**Parameters:**
- `placement_key` (str, path): External identifier (e.g., "homepage_top")

**Response:**
```json
{
  "placement_id": 10,
  "tenant_id": 1,
  "placement_key": "homepage_top",
  "name": "Homepage Top Banner",
  "status": "active",
  "min_width_px": 728,
  "max_width_px": 728,
  "min_height_px": 90,
  "max_height_px": 90,
  "responsive": false,
  "metadata": {
    "section": "header",
    "priority": "high"
  },
  "created_at": "2024-01-01T10:00:00Z",
  "updated_at": "2024-01-15T14:30:00Z"
}
```

### Ad Serving

**GET** `/serve/{placement_ref}`
Assemble a full ad-serving payload for a placement or ad.

**Parameters:**
- `placement_ref` (str, path): Placement key or ad key
- `tenant` (str, query): Tenant key (default: "demo")
- `limit` (int, query): Max ads to return (default: 5)

**Response:**
```json
{
  "ads": [
    {
      "ad_id": 1,
      "ad_key": "ad_1001",
      "creative_id": 500,
      "campaign_id": 100,
      "placement_id": 10,
      "status": "active",
      "score_weight": 2.0,
      "frequency_cap": 3,
      "metadata": {}
    }
  ]
}
```

**Note:** This is a development/test endpoint; production usage should implement proper tenant authentication via JWT/OAuth.

---

## Data Models

The ad server is built on four core entities: **Placement**, **Campaign**, **Creative**, and **Ad**.

```
Placement (inventory)
    ↓ (1:M)
Ad (delivery config)
    ├─ → Campaign (buying/business logic)
    ├─ → Creative (content/assets)
    └─ → Tenant (multi-tenancy)
```

### Placement

Publisher inventory slot. Represents a stable, named location on a website/app.

**Schema:** `leo_ads.placement`

**Key fields:**
- `placement_id` (BigInteger, PK): Internal surrogate key
- `placement_key` (String): External identifier (e.g., "homepage_top") — **use this for APIs**
- `tenant_id` (BigInteger, FK): Multi-tenant isolation
- `name` (String): Display name
- `status` (String): `'active' | 'paused' | 'archived'`
- `min_width_px`, `max_width_px`, `min_height_px`, `max_height_px` (Integer, nullable): Dimension constraints
- `responsive` (Boolean): Whether placement supports responsive sizing
- `metadata` (JSONB): Extensible placement-specific attributes
- `created_at`, `updated_at` (DateTime): Audit timestamps

**Example placements:**
```
homepage_top        → 728×90 banner
article_inline      → 300×250 / 336×280
sidebar             → 300×600 / 320×600
native_feed         → responsive, flexible
```

### Campaign

Represents the business/buying configuration. One campaign may fund multiple ads across different placements.

**Schema:** `leo_ads.campaign`

**Key fields:**
- `campaign_id` (BigInteger, PK): Internal ID
- `campaign_key` (String): External identifier — **use this for APIs**
- `tenant_id` (BigInteger, FK): Multi-tenant isolation
- `advertiser_id` (BigInteger, FK, nullable): Advertiser owner
- `source_account_id` (BigInteger, FK, nullable): Source/provider account (Google Ads, Shopee, etc.)
- `name` (String): Campaign display name
- `objective` (String): Campaign goal (`'awareness' | 'traffic' | 'conversions' | 'retention' | ...`)
- `buying_model` (String): Pricing model (`'CPM' | 'CPC' | 'CPA' | 'vCPM' | ...`)
- `budget_amount` (Decimal): Total budget
- `currency` (String): ISO 4217 code (e.g., 'USD')
- `daily_budget_amount` (Decimal, nullable): Daily cap
- `status` (String): `'draft' | 'approved' | 'running' | 'paused' | 'ended' | 'archived'`
- `starts_at`, `ends_at` (DateTime, nullable): Campaign date range
- `metadata` (JSONB): Campaign-specific config (targeting rules, bid strategy, etc.)

### Creative

Represents reusable ad content/assets. Multiple ads can reference the same creative.

**Schema:** `leo_ads.creative`

**Key fields:**
- `creative_id` (BigInteger, PK): Internal ID
- `creative_key` (String): External identifier — **use this for APIs**
- `tenant_id` (BigInteger, FK): Multi-tenant isolation
- `ad_type` (String): `'display' | 'native' | 'video' | 'carousel' | 'dynamic' | ...`
- `format_code` (String): Dimensions/layout (`'300x250' | '728x90' | '1200x628' | 'responsive' | ...`)
- `status` (String): `'active' | 'paused' | 'archived'`
- `version_no` (Integer): Content version tracking
- `headline`, `subheadline`, `body`, `cta` (Text): Common rendering fields
- `image_url`, `video_url`, `logo_url` (Text): Asset URLs
- `content_payload` (JSONB): Provider-specific payload (e.g., Google Ad Manager JSON, Shopee widget config)
- `created_at`, `updated_at` (DateTime): Audit timestamps

**Design note:**
Creative separates **content** (text, images, media) from **delivery config** (Ad) and **business config** (Campaign). This enables:
- Multiple Ads to reference one Creative (reuse)
- Multiple Campaigns to share Creative variants
- Flexible provider-specific data in `content_payload` without schema migrations
- Easy A/B testing by swapping creatives

### Ad

Represents the serving configuration linking Campaign, Creative, and Placement.

**Schema:** `leo_ads.ad`

**Key fields:**
- `ad_id` (BigInteger, PK): Internal surrogate key
- `ad_key` (String): External identifier — **use this for APIs, never expose `ad_id`**
- `tenant_id` (BigInteger, FK): Multi-tenant isolation
- `campaign_id` (BigInteger, FK, nullable): Link to Campaign (optional; ads may exist without campaigns)
- `creative_id` (BigInteger, FK): Link to Creative content
- `placement_id` (BigInteger, FK): Link to Placement inventory
- `status` (String): `'active' | 'paused' | 'archived'` — only active ads serve
- `score_weight` (Float): Ranking weight for candidate selection (higher = preferred)
- `frequency_cap` (Integer, nullable): Max impressions per user per session/day
- `metadata` (JSONB): Ad-tech-specific configuration (targeting rules, bid adjustments, etc.)
- `created_at`, `updated_at` (DateTime): Audit timestamps

**Multi-tenancy:** ⚠️  **CRITICAL**: Every query must filter by `tenant_id` to prevent cross-tenant data exposure.

**Example query (from AdRepository):**
```python
statement = (
    select(Ad)
    .where(
        Ad.tenant_id == tenant_id,      # ← MANDATORY
        Ad.placement_id == placement_id,
        Ad.status == "active",
    )
    .order_by(Ad.score_weight.desc(), Ad.ad_id.asc())
    .limit(20)
)
```

### Supporting Models

**Tenant**
- Represents an isolated tenant/customer
- All tables reference `tenant_id`
- Enforce tenant isolation on all queries

**Advertiser**
- Represents a brand/advertiser within a tenant
- Campaigns link to advertisers (optional)

**SourceAccount**
- Represents an external provider account (Google Ads, Shopee, Lazada, etc.)
- Links to advertiser and tenant
- Stores provider-specific credentials/config

**SourceAsset**
- External assets managed by providers (Google campaigns, Shopee offers, etc.)
- Maps external IDs to local campaign/creative/ad IDs

---

## Project Structure

```
ads-server/
├── app.py                      # FastAPI entrypoint & local dev runner
├── core/
│   ├── __init__.py
│   ├── application.py          # Application composition root, routing, lifecycle
│   ├── config.py               # Pydantic settings, database engine, connection pooling
│   └── database.py             # Database utilities (future)
├── model/
│   ├── __init__.py
│   ├── base.py                 # SQLAlchemy DeclarativeBase
│   ├── ad.py                   # Ad ORM model
│   ├── campaign.py             # Campaign ORM model
│   ├── creative.py             # Creative ORM model
│   ├── placement.py            # Placement ORM model
│   └── tenant.py               # Tenant ORM model
├── repository/
│   ├── __init__.py
│   ├── ad_repository.py        # Ad query operations
│   ├── placement_repository.py # Placement query operations
│   └── ad_cache_utils.py       # Redis caching utilities (future)
├── static/
│   ├── ads.loader.js           # Client-side ad widget & loader
│   ├── leo.ads.css             # Ad widget styling (light/dark theme)
│   ├── ads-banner.html         # HTML test page for ad rendering
│   └── ads.data.json           # Static test data fixture
├── sql-scripts/
│   ├── db-schema-init.sql      # PostgreSQL schema creation
│   └── sample-data-init.sql    # Demo data seeding
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # Pytest fixtures (test DB, sessions, etc.)
│   ├── _pg.py                  # PostgreSQL availability check
│   ├── test_api.py             # Integration tests for endpoints
│   ├── test_models.py          # ORM field mapping tests
│   ├── test_repositories.py    # Repository query logic tests
│   └── test_model_metadata.py  # Model introspection tests
├── logs/                        # Application logs (created at runtime)
├── .env.example                 # Environment variable template
├── .env                         # Actual env config (git-ignored)
├── .gitignore
├── .dockerignore
├── Dockerfile                   # Container image definition
├── requirements.txt             # Python dependencies
├── start.sh                     # Startup script (with optional --seed-demo-ads-server)
├── stop.sh                      # Shutdown script
├── restart.sh                   # Stop + start convenience script
├── run_unit_tests.sh            # Test runner
└──  README.md                    # This file
```

**Key files explained:**

- **app.py**: Entry point. Bootstraps `AdServerApplication` and exposes `app` object for uvicorn
- **core/application.py**: Composition root; creates FastAPI instance, registers routes, manages lifecycle
- **core/config.py**: Loads `.env`, creates SQLAlchemy engine with connection pooling
- **model/*.py**: SQLAlchemy ORM definitions (declarative syntax)
- **repository/*.py**: Persistence layer; all database queries live here
- **static/**: Frontend assets (JS widget, CSS, HTML test pages)
- **sql-scripts/**: DDL for schema initialization and DML for seed data
- **tests/**: Full test suite with fixtures for test database

---

## Repositories

**Repositories** encapsulate all database queries and serialization. Business logic belongs in **Services** (future).

### AdRepository

**Located in:** `repository/ad_repository.py`

**Responsibilities:**
- Retrieve ads by ID
- Retrieve active ads for a placement (hot path)
- Serialize ORM objects to API-safe dicts

**Methods:**

#### `get_by_id(ad_id: int) -> dict | None`

Retrieve a single ad by primary key.

```python
from repository.ad_repository import AdRepository

repo = AdRepository(engine=db_engine)
ad = repo.get_by_id(ad_id=12345)
# Returns: {"ad_id": 12345, "tenant_id": 1, "ad_key": "ad_1001", ...} or None
```

#### `get_active_by_placement(tenant_id: int, placement_id: int, limit: int = 20) -> list[dict]`

Retrieve active ads for a placement (production hot path).

```python
repo = AdRepository(engine=db_engine)
ads = repo.get_active_by_placement(
    tenant_id=1,
    placement_id=10,
    limit=20
)
# Returns: [{"ad_id": 1, ...}, {"ad_id": 2, ...}, ...]
```

**Performance notes:**
- Uses indexed fields: `(tenant_id, placement_id, status)`
- Results ordered by `score_weight DESC, ad_id ASC` for deterministic ranking
- Limits to max 100 results to prevent over-fetching
- Should be cached in Redis with TTL=300 seconds
- Every query includes `tenant_id` filter (multi-tenancy isolation)

#### `get_serving_ads(tenant_key: str, placement_ref: str, limit: int = 5) -> list[dict]`

High-level serving endpoint combining placement + ad queries.

```python
repo = AdRepository(engine=db_engine)
ads = repo.get_serving_ads(
    tenant_key="demo",
    placement_ref="homepage_top",  # Can also be an ad_key
    limit=5
)
# Returns: [{"ad_id": 1, ...}, {"ad_id": 2, ...}, ...]
```

### PlacementRepository

**Located in:** `repository/placement_repository.py`

**Responsibilities:**
- Retrieve active placements by key
- Optional tenant filtering
- Serialize ORM objects to API-safe dicts

**Not responsible for:**
- Validating placement constraints (width/height)
- Fetching associated formats (PlacementFormat table)
- Fetching candidate ads (use AdRepository)
- Caching (should migrate to RedisRepository)

**Methods:**

#### `get_active_by_key(placement_key: str, tenant_id: int | None = None) -> dict | None`

Retrieve an active placement by its external key.

```python
from repository.placement_repository import PlacementRepository

repo = PlacementRepository(engine=db_engine)
placement = repo.get_active_by_key(
    placement_key="homepage_top",
    tenant_id=1
)
# Returns: {"placement_id": 10, "placement_key": "homepage_top", ...} or None
```

**Performance notes:**
- Placement lookups should be cached in Redis (TTL=3600 seconds)
- No joins; plain SELECT
- Tenant filtering is currently optional but should become mandatory
- Future versions will enforce `tenant_id` on all queries

---

## Testing

### Running Tests

```bash
# Run all tests
./run_unit_tests.sh

# Run specific test file
.venv/bin/python -m pytest tests/test_models.py -v

# Run with coverage report
.venv/bin/python -m pytest tests/ --cov=. --cov-report=html

# Run only integration tests (requires PostgreSQL)
.venv/bin/python -m pytest tests/test_api.py -v

# Run specific test function
.venv/bin/python -m pytest tests/test_api.py::test_get_placement -v
```

### Test Structure

#### Unit Tests: `tests/test_models.py`
- Test ORM field definitions and mappings
- Verify model constraints and defaults
- Test SQLAlchemy type system
- No database required (SQLAlchemy type checks only)

#### Unit Tests: `tests/test_repositories.py`
- Test repository query logic
- Test serialization (_to_dict methods)
- Test edge cases (NULL values, empty results, multi-tenancy)
- Verifies queries return correct structure

#### Integration Tests: `tests/test_api.py`
- Test HTTP endpoints via TestClient
- Test request/response serialization
- Verify multi-tenancy isolation
- Test health checks and error handling
- **Requires PostgreSQL** — creates a transactional test database

#### Model Metadata Tests: `tests/test_model_metadata.py`
- Test ORM introspection
- Verify table names, column names, relationships

### Test Database

Tests use transactional fixtures (from `conftest.py`) that:
1. Create a test database connection
2. Start a SAVEPOINT before each test
3. Roll back the SAVEPOINT after each test (no data persists)

**Requirement:** PostgreSQL must be running; SQLite is not compatible due to:
- Identity columns (GENERATED ALWAYS AS IDENTITY)
- Foreign key cascades
- JSONB columns
- Type-specific features

### Pytest Fixtures

**From `tests/conftest.py`:**

- `test_engine`: SQLAlchemy Engine connected to test database
- `test_session`: Transactional session for queries
- `seed`: Fixture providing sample tenant_id, campaign_id, creative_id
- `test_app`: FastAPI test application
- `client`: TestClient for making HTTP requests
- `sample_placement`: Pre-seeded Placement object

---

## Database Schema

### Overview

All tables are in the **`leo_ads`** schema. The schema supports:
- Multi-tenancy via `tenant_id` on every table
- Flexible provider integration (Google Ads, Shopee, Lazada, etc.)
- Extensible metadata (JSONB) on most tables
- Historical tracking (created_at, updated_at)

### Initialization

Initialize the database with:
```bash
./start.sh --seed-demo-ads-server
```

This:
1. Creates the `leo_ads` schema
2. Creates all tables (from `sql-scripts/db-schema-init.sql`)
3. Seeds demo data (from `sql-scripts/sample-data-init.sql`)
4. Starts the API server

### Connection Configuration

From `.env`:
```bash
LEO_AD_DB_HOST=localhost
LEO_AD_DB_PORT=5432
LEO_AD_DB_USER=postgres
LEO_AD_DB_PASSWORD=your_password
LEO_AD_DB_NAME=customer360
LEO_AD_DB_SCHEMA=leo_ads
LEO_AD_DB_POOL_SIZE=10
LEO_AD_DB_MAX_OVERFLOW=20
LEO_AD_DB_ECHO_SQL=false  # Set to true for SQL debugging
```

**Connection pooling:**
- Pool size: 10 connections (default)
- Max overflow: 20 (temporary connections during spikes)
- Recycle: 1800 seconds (30 min) — prevents stale connections
- Pre-ping: Enabled — verifies connection health before reuse

### Core Tables

**leo_ads.tenant**
- Represents an isolated customer/tenant
- All other tables reference `tenant_id`

**leo_ads.advertiser**
- Brand/advertiser within a tenant
- Campaigns optionally link to advertisers

**leo_ads.source_account**
- External provider account (Google Ads, Shopee, etc.)
- Stores provider credentials and configuration

**leo_ads.source_asset**
- External assets from providers
- Maps external IDs (Google campaign ID, Shopee offer ID) to local IDs

**leo_ads.placement**
- Publisher inventory slots
- Dimensions, responsiveness, and constraints
- `(tenant_id, placement_key)` unique

**leo_ads.placement_format**
- Supported formats for a placement
- Stores width, height, responsive, and format-specific constraints

**leo_ads.campaign**
- Business/buying configuration
- Budget, objective, date range, status
- `(tenant_id, campaign_key)` unique

**leo_ads.creative**
- Reusable ad content and assets
- Text, images, videos, metadata
- Provider-specific payload in `content_payload`
- `(tenant_id, creative_key)` unique

**leo_ads.ad**
- Serving configuration: Campaign + Creative + Placement
- Status, ranking weight, frequency cap
- `(tenant_id, ad_key)` unique

### Key Constraints

1. **Multi-tenancy:** Every query must filter by `tenant_id` to prevent cross-tenant leakage
2. **Foreign keys:** 
   - `Ad → Campaign` (ON DELETE SET NULL, nullable)
   - `Ad → Creative` (ON DELETE RESTRICT, protected)
   - `Ad → Placement` (ON DELETE RESTRICT, protected)
3. **Unique keys:** All external identifiers (`*_key` columns) are unique per tenant
4. **Indexes:** Optimized for hot paths:
   - `(tenant_id, placement_id, status)` for ad serving
   - `(tenant_id, placement_key)` for placement lookups
   - `(tenant_id, campaign_key)`, `(tenant_id, creative_key)`, etc.

### Important Notes

- **No soft deletes** — use `status = 'archived'` instead
- **JSONB columns** store provider-specific, extensible data
- **Type safety** — use PostgreSQL types (BIGINT, DECIMAL, etc.) not application types
- **Concurrency** — connection pooling handles multiple concurrent requests

---

## Development Guidelines

### Code Architecture

The ad server follows **clean layering** to keep code maintainable and testable:

```
HTTP Layer (FastAPI endpoints) → Requests & Response serialization
    ↓
Repository Layer (persistence) → Database queries & ORM mapping
    ↓
SQLAlchemy ORM (models) → Object-relational mapping
    ↓
PostgreSQL (durability) → Source of truth
```

**Separation of concerns:**

- **Models** (`model/`): ORM definitions only; no business logic
- **Repositories** (`repository/`): Database queries only; no HTTP or business logic
- **Application** (`core/application.py`): Routing, lifecycle, dependency injection
- **Handlers** (in `core/application.py`): HTTP request/response; minimal logic
- **Services** (future): Business logic (targeting, ranking, caching, etc.)

### Adding a New Endpoint

1. **Add repository method** for database access:
   ```python
   # repository/ad_repository.py
   def get_by_campaign(self, tenant_id: int, campaign_id: int) -> list[dict]:
       """Get all active ads for a campaign."""
       # Query logic...
   ```

2. **Add route handler** in `core/application.py`:
   ```python
   def get_campaign_ads(self, campaign_id: int):
       """GET /campaigns/{campaign_id}/ads"""
       return self.ad_repository.get_by_campaign(
           tenant_id=1,  # TODO: Extract from JWT/auth context
           campaign_id=campaign_id,
       )
   ```

3. **Register route**:
   ```python
   # In _register_routes()
   application.add_api_route(
       "/campaigns/{campaign_id}/ads",
       self.get_campaign_ads,
       methods=["GET"],
       tags=["Ads"],
   )
   ```

4. **Add tests**:
   - Unit test in `tests/test_repositories.py`
   - Integration test in `tests/test_api.py`

5. **Update README** with endpoint documentation

### Code Quality Standards

1. **Always filter by `tenant_id`** in queries to prevent cross-tenant data leakage
   ```python
   # ✅ Correct
   select(Ad).where(Ad.tenant_id == tenant_id, Ad.status == "active")
   
   # ❌ Wrong
   select(Ad).where(Ad.status == "active")  # Missing tenant_id!
   ```

2. **Use repositories** for all database access (never write SQL in handlers)
   ```python
   # ✅ Correct
   def get_ad(self, ad_id: int):
       return self.ad_repository.get_by_id(ad_id)
   
   # ❌ Wrong
   def get_ad(self, ad_id: int):
       session = Session(bind=self.engine)
       return session.query(Ad).filter(Ad.ad_id == ad_id).one()
   ```

3. **Add docstrings** to all public methods:
   ```python
   def get_active_by_placement(
       self,
       tenant_id: int,
       placement_id: int,
       limit: int = 20,
   ) -> list[dict]:
       """
       Retrieve active ads for a placement.
       
       Args:
           tenant_id: Tenant to filter by (required for multi-tenancy)
           placement_id: Placement to fetch ads for
           limit: Max results (default: 20, max: 100)
       
       Returns:
           List of ad dicts with serialized fields
       """
   ```

4. **Use type hints** on all function signatures:
   ```python
   # ✅ Correct
   def get_by_id(self, ad_id: int) -> dict | None:
       pass
   
   # ❌ Wrong
   def get_by_id(self, ad_id):
       pass
   ```

5. **Validate inputs** in repositories and services:
   ```python
   def get_by_id(self, ad_id: int) -> dict | None:
       if ad_id <= 0:
           raise ValueError("ad_id must be positive")
       # Query...
   ```

6. **Handle errors explicitly**:
   ```python
   # ✅ Correct - re-raise or handle
   try:
       with self.engine.connect() as conn:
           conn.execute(text("SELECT 1"))
   except Exception as e:
       logger.exception("Database connection failed")
       raise
   
   # ❌ Wrong - silent failure
   try:
       # ...
   except Exception:
       pass
   ```

### Multi-Tenancy Best Practices

- Always validate tenant context before returning data
- Use `(tenant_id, *_key)` composite unique constraints
- Include `tenant_id` in all WHERE clauses
- Never assume tenant context from user input alone (use JWT/auth)
- Test multi-tenancy isolation in integration tests

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `LEO_AD_API_HOST` | `localhost` | API listen address |
| `LEO_AD_API_PORT` | `9009` | API listen port |
| `LEO_AD_DB_HOST` | `localhost` | PostgreSQL host |
| `LEO_AD_DB_PORT` | `5432` | PostgreSQL port |
| `LEO_AD_DB_USER` | `postgres` | PostgreSQL user |
| `LEO_AD_DB_PASSWORD` | `password` | PostgreSQL password |
| `LEO_AD_DB_NAME` | `customer360` | Database name |
| `LEO_AD_DB_SCHEMA` | `leo_ads` | Schema name |
| `LEO_AD_DB_POOL_SIZE` | `10` | Connection pool size |
| `LEO_AD_DB_MAX_OVERFLOW` | `20` | Max overflow connections |
| `LEO_AD_DB_POOL_RECYCLE_SECONDS` | `1800` | Connection recycle interval |
| `LEO_AD_DB_ECHO_SQL` | `false` | Enable SQL logging (debug) |
| `LEO_AD_UVICORN_RELOAD` | `false` | Enable auto-reload (dev only) |
| `LEO_AD_CACHE_ENABLED` | `true` | Enable Redis caching |
| `LEO_AD_REDIS_HOST` | `localhost` | Redis host |
| `LEO_AD_REDIS_PORT` | `6580` | Redis port |
| `LEO_AD_REDIS_DB` | `0` | Redis database number |
| `LEO_AD_REDIS_PASSWORD` | unset | Redis password |
| `LEO_AD_CACHE_TTL_SECONDS` | `60` | Cache TTL |

---

## Performance Tips

1. **Cache placements**: Placement lookups have 90% read ratio → cache in Redis
2. **Cache ad candidates**: Pre-compute `placement_id → [ad_ids]` in Redis
3. **Batch ad queries**: Use `get_active_by_placement` instead of loop of `get_by_id`
4. **Connection pooling**: SQLAlchemy uses configured pool (default: 10 connections, recycle every 30 min)
5. **Query optimization**: All hot queries use indexed fields: `(tenant_id, placement_id, status)`

---

## Production Deployment Checklist

- [ ] Database is PostgreSQL 16+
- [ ] Implement JWT/OAuth authentication middleware
- [ ] Enforce `tenant_id` extraction from auth context
- [ ] Add request logging and distributed tracing (OpenTelemetry)
- [ ] Set up Redis caching for `placement_key` → Placement lookups
- [ ] Implement ad ranking service (not just score_weight ordering)
- [ ] Add pagination to list endpoints
- [ ] Implement input validation with Pydantic models
- [ ] Add rate limiting and DDoS protection
- [ ] Set up monitoring, alerting, and health checks
- [ ] Configure database connection pooling
- [ ] Enable SSL/TLS for database and API
- [ ] Rotate secrets (DB password, API keys)
- [ ] Document SLAs and performance targets
- [ ] Load test with realistic placement and ad volumes

---

## Monitoring & Observability

### Health Checks

API provides three health endpoints for monitoring:

```bash
# Service is running
curl http://localhost:9009/health

# Database is reachable
curl http://localhost:9009/health/database

# Basic info
curl http://localhost:9009/
```

### Logging

Logs are written to `logs/app.log` with timestamps.

---

## Troubleshooting

### PostgreSQL connection error

```
PostgreSQL connection failed
```

**Solution:**
- Ensure PostgreSQL is running: `docker ps | grep postgres`
- Check credentials in `.env`
- Verify `LEO_AD_DB_HOST` is reachable: `ping localhost`

### Schema not found

```
ERROR:  schema "leo_ads" does not exist
```

**Solution:**
```bash
./start.sh --seed-demo-ads-server
```

This creates the schema and seeds demo data.

### Ad server already running

```
[API] Already running | PID 12345
[API] Stop first | ./stop.sh
```

**Solution:**
```bash
./stop.sh
./start.sh
```

---

## Contributing

### Code Style

- Follow PEP 8
- Use type hints
- Document public APIs with docstrings
- Add tests for new features

### Pull Request Checklist

- [ ] Tests pass: `./run_unit_tests.sh`
- [ ] Code is documented
- [ ] Multi-tenancy is preserved (tenant_id filtering)
- [ ] No SQL injection vectors (use parameterized queries)
- [ ] README is updated if needed

---

## Links

- **PostgreSQL schema**: See `sql-scripts/db-schema-init.sql` for full table definitions
- **Sample data**: See `sql-scripts/sample-data-init.sql` for demo dataset
- **FastAPI docs**: https://fastapi.tiangolo.com
- **SQLAlchemy 2.0**: https://docs.sqlalchemy.org
- **Pydantic**: https://docs.pydantic.dev

---

## License

See LICENSE file in repository root.
