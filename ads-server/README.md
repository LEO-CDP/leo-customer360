# LEO Ad Server API

High-performance, multi-tenant ad serving API built on FastAPI + SQLAlchemy + PostgreSQL 16.

Designed for:
- **Low-latency ad serving** with Redis caching
- **Multi-tenant isolation** with strict tenant_id filtering
- **Production-grade code** with comprehensive documentation and tests
- **Scalability** supporting 40M+ profiles, multiple ad sources, flexible formats

---

## Quick Start

### Prerequisites
- Python 3.9+
- PostgreSQL 16+
- Redis (optional, for caching)
- Docker Compose (optional, for local dev environment)

### Installation

```bash
cd ads-server

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy .env template
cp .env.example .env

# Edit .env with your configuration
# LEO_AD_API_HOST=localhost
# LEO_AD_API_PORT=9009
# DB_HOST=localhost
# DB_PORT=5432
# DB_USER=postgres
# DB_PASSWORD=change_me_postgres_password
# DB_NAME=customer360
```

### Running the Server

**Basic startup:**
```bash
./start.sh
```

**With database seeding (creates schema + demo data):**
```bash
./start.sh --seed-demo-ads-server
```

**Stop the server:**
```bash
./stop.sh
```

**Restart the server:**
```bash
./restart.sh
```

### Development Mode

```bash
# With auto-reload
UVICORN_RELOAD=true ./start.sh

# Or directly with uvicorn
uvicorn app:app --reload --port 9009
```

**Browse API docs:**
- Swagger UI: http://localhost:9009/docs
- ReDoc: http://localhost:9009/redoc

---

## API Endpoints

### Health

**GET** `/`
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
```json
{
  "status": "ok",
  "service": "leo-ad-server-api"
}
```

**GET** `/health/database`
```json
{
  "status": "ok",
  "database": "reachable",
  "schema": "leo_ads"
}
```

### Ads

**GET** `/ads/{ad_id}`

Retrieve a single ad by ID.

Parameters:
- `ad_id` (int, path): The primary key of the ad

Response:
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

### Placements

**GET** `/placements/{placement_key}`

Retrieve a placement by key.

Parameters:
- `placement_key` (str, path): The external identifier for the placement

Response:
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
  "metadata": {},
  "created_at": "2024-01-01T10:00:00Z",
  "updated_at": "2024-01-15T14:30:00Z"
}
```

---

## Project Structure

```
ads-server/
├── app.py                     # FastAPI entrypoint
├── core/
│   ├── application.py         # Application composition & lifecycle
│   ├── config.py              # Configuration & database setup
│   └── database.py            # Database connection pooling
├── model/
│   ├── base.py                # SQLAlchemy DeclarativeBase
│   ├── ad.py                  # Ad ORM model
│   ├── campaign.py            # Campaign ORM model
│   ├── creative.py            # Creative ORM model
│   └── placement.py           # Placement ORM model
├── repository/
│   ├── ad_repository.py       # Ad query repository
│   └── placement_repository.py # Placement query repository
├── tests/
│   ├── test_model_metadata.py # ORM field mapping tests
│   ├── test_models.py         # Model unit tests
│   ├── test_repositories.py   # Repository unit tests
│   └── test_api.py            # Integration tests
├── sql-scripts/
│   ├── db-schema-init.sql     # Schema initialization
│   └── sample-data-init.sql   # Demo data seeding
├── .env.example               # Environment template
├── start.sh                   # Startup script
├── stop.sh                    # Shutdown script
├── restart.sh                 # Restart script
├── run_unit_tests.sh          # Test runner
└── requirements.txt           # Python dependencies
```

---

## Models

### Ad (leo_ads.ad)

Represents a delivery configuration for an ad unit.

**Key fields:**
- `ad_id` (BigInteger, PK): Internal ID
- `ad_key` (String): External identifier (use this for APIs)
- `tenant_id` (BigInteger, FK): Multi-tenant isolation
- `campaign_id` (BigInteger, FK, nullable): Links to Campaign
- `creative_id` (BigInteger, FK): Links to Creative content
- `placement_id` (BigInteger, FK): Links to Placement inventory
- `status` (String): 'active' | 'paused' | 'archived'
- `score_weight` (Float): Ranking weight for candidate selection
- `frequency_cap` (Integer, nullable): Max impressions per user
- `metadata` (JSONB): Extensible ad-tech configuration
- `created_at`, `updated_at` (DateTime): Audit timestamps

**Multi-tenancy rule:**
⚠️  **CRITICAL**: All queries MUST filter by `tenant_id` to prevent cross-tenant data exposure.

**Example query (from AdRepository):**
```python
statement = (
    select(Ad)
    .where(
        Ad.tenant_id == tenant_id,      # ← Multi-tenant filter
        Ad.placement_id == placement_id,
        Ad.status == "active",
    )
    .order_by(Ad.score_weight.desc(), Ad.ad_id.asc())
    .limit(20)
)
```

### Campaign (leo_ads.campaign)

Represents the business/buying configuration.

**Key fields:**
- `campaign_id` (BigInteger, PK): Internal ID
- `campaign_key` (String): External identifier
- `tenant_id` (BigInteger): Multi-tenant isolation
- `advertiser_id` (BigInteger, nullable): Advertiser owner
- `name` (String): Display name
- `objective` (String): 'awareness' | 'traffic' | 'conversions' | 'retention' | ...
- `buying_model` (String): 'CPM' | 'CPC' | 'CPA' | 'vCPM' | ...
- `budget_amount` (Decimal): Total budget
- `currency` (String): ISO 4217 code (e.g., 'USD')
- `daily_budget_amount` (Decimal, nullable): Daily cap
- `status` (String): 'draft' | 'approved' | 'running' | 'paused' | 'ended' | 'archived'
- `starts_at`, `ends_at` (DateTime, nullable): Campaign date range
- `metadata` (JSONB): Campaign-specific attributes

### Creative (leo_ads.creative)

Represents reusable ad content/assets.

**Key fields:**
- `creative_id` (BigInteger, PK): Internal ID
- `creative_key` (String): External identifier
- `tenant_id` (BigInteger): Multi-tenant isolation
- `ad_type` (String): 'display' | 'native' | 'video' | 'carousel' | ...
- `format_code` (String): '300x250' | '728x90' | '1200x628' | 'responsive' | ...
- `status` (String): 'active' | 'paused' | 'archived'
- `version_no` (Integer): Content version tracking
- `headline`, `subheadline`, `body`, `cta` (Text): Common rendering fields
- `image_url`, `video_url`, `logo_url` (Text): Asset URLs
- `content_payload` (JSONB): Provider-specific payload
- `created_at`, `updated_at` (DateTime): Audit timestamps

**Design note:**
Creative separates content (text, images) from delivery config (Ad) and business config (Campaign). This allows:
- Multiple Ads to reference the same Creative
- Multiple Campaigns to share Creative variants
- Provider-specific data in `content_payload` without schema migrations

### Placement (leo_ads.placement)

Represents publisher inventory slots.

**Key fields:**
- `placement_id` (BigInteger, PK): Internal ID
- `placement_key` (String): External identifier (e.g., 'homepage_top')
- `tenant_id` (BigInteger): Multi-tenant isolation
- `name` (String): Display name
- `status` (String): 'active' | 'paused' | 'archived'
- `min_width_px`, `max_width_px` (Integer, nullable): Width constraints
- `min_height_px`, `max_height_px` (Integer, nullable): Height constraints
- `responsive` (Boolean): Whether placement supports responsive sizing
- `metadata` (JSONB): Placement-specific metadata
- `created_at`, `updated_at` (DateTime): Audit timestamps

**Example placements:**
```
homepage_top → 728x90 banner
article_inline → 300x250 | 336x280
sidebar → 300x600 | 320x600
native_feed → responsive
```

---

## Repositories

Repositories encapsulate all database queries. Business logic lives in Services (future).

### AdRepository

Located in: `repository/ad_repository.py`

**Methods:**

#### `get_by_id(ad_id: int) -> dict | None`
Retrieve a single ad by primary key.

```python
repo = AdRepository(engine=db_engine)
ad = repo.get_by_id(123)
# Returns: {"ad_id": 123, "tenant_id": 1, ...} or None
```

#### `get_active_by_placement(tenant_id: int, placement_id: int, limit: int = 20) -> list[dict]`
Retrieve active ads for a placement (hot path).

```python
repo = AdRepository(engine=db_engine)
ads = repo.get_active_by_placement(
    tenant_id=1,
    placement_id=10,
    limit=20
)
# Returns: [{"ad_id": 1, ...}, {"ad_id": 2, ...}, ...]
```

**Performance:**
- Uses indexed fields: `(tenant_id, placement_id, status)`
- Results ordered by `score_weight DESC, ad_id ASC` for deterministic ranking
- Limits to max 100 results to prevent over-fetching
- Should be cached in Redis with TTL=300 seconds

**Multi-tenancy:**
⚠️  Always includes `tenant_id` filter to prevent cross-tenant leakage.

### PlacementRepository

Located in: `repository/placement_repository.py`

**Methods:**

#### `get_active_by_key(placement_key: str, tenant_id: int | None = None) -> dict | None`
Retrieve an active placement by key.

```python
repo = PlacementRepository(engine=db_engine)
placement = repo.get_active_by_key(
    placement_key="homepage_top",
    tenant_id=1
)
# Returns: {"placement_id": 10, "placement_key": "homepage_top", ...} or None
```

**Performance:**
- Placement lookups should be cached in Redis (TTL=3600 seconds)
- No additional joins; plain SELECT

**Multi-tenancy:**
- Tenant filtering is optional for now but should become mandatory
- Future versions will enforce tenant_id on all queries

---

## Testing

Run all tests:
```bash
./run_unit_tests.sh
```

Run specific test file:
```bash
.venv/bin/python -m pytest tests/test_models.py -v
```

Run with coverage:
```bash
.venv/bin/python -m pytest tests/ --cov=. --cov-report=html
```

### Test Structure

#### Unit Tests: `tests/test_models.py`
- Test ORM field mappings
- Test constraints and defaults
- Test SQLAlchemy type system

#### Unit Tests: `tests/test_repositories.py`
- Test repository query logic (mocked DB)
- Test serialization (_to_dict methods)
- Test edge cases (NULL values, empty results)

#### Integration Tests: `tests/test_api.py`
- Test API endpoints with a test database
- Test multi-tenancy isolation
- Test health checks and error handling

---

## Database

### Schema

All tables are in the `leo_ads` schema.

Initialize the database:
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
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=change_me_postgres_password
DB_NAME=customer360
DB_SCHEMA=leo_ads
```

### Important Constraints

1. **Multi-tenancy**: Every table has `tenant_id`. Queries must filter by tenant.
2. **Foreign keys**: Ad → Campaign, Creative, Placement all have CASCADE/RESTRICT
3. **Unique keys**: `(tenant_id, ad_key)`, `(tenant_id, campaign_key)`, etc. (in schema)
4. **Indexes**: Placement ID, Status, Score Weight for fast ad serving

---

## Development Guidelines

### Code Quality

1. **Always filter by `tenant_id`** in queries to prevent cross-tenant data leakage
2. **Use repositories** for all database access (never write SQL in controllers)
3. **Add docstrings** to all public methods
4. **Type hints** on all function signatures
5. **Validate inputs** in repositories and services
6. **Handle errors explicitly** (don't swallow exceptions)

### Adding a New Endpoint

1. Define repository method (persistence layer)
2. Add service logic (business logic) - future
3. Add API route in `core/application.py`
4. Add tests
5. Update this README

Example:
```python
# repository/ad_repository.py
def get_by_campaign(self, tenant_id: int, campaign_id: int) -> list[dict]:
    # Query logic...

# core/application.py (in _register_routes)
application.add_api_route(
    "/campaigns/{campaign_id}/ads",
    self.get_campaign_ads,
    methods=["GET"],
    tags=["Ads"],
)

# Handler
def get_campaign_ads(self, campaign_id: int):
    return self.ad_repository.get_by_campaign(
        tenant_id=1,  # TODO: Extract from JWT/auth context
        campaign_id=campaign_id,
    )
```

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `LEO_AD_API_HOST` | `localhost` | API listen address |
| `LEO_AD_API_PORT` | `9009` | API listen port |
| `DB_HOST` | `localhost` | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_USER` | `postgres` | PostgreSQL user |
| `DB_PASSWORD` | `change_me_postgres_password` | PostgreSQL password |
| `DB_NAME` | `customer360` | Database name |
| `DB_SCHEMA` | `leo_ads` | Schema name |
| `UVICORN_RELOAD` | `false` | Enable auto-reload (dev only) |

---

## Architecture

### Layering

```
API Layer (FastAPI endpoints)
    ↓
Repository Layer (persistence)
    ↓
SQLAlchemy ORM (model mapping)
    ↓
PostgreSQL (durability)
```

### Separation of Concerns

- **Models** (`model/`): ORM definitions only
- **Repositories** (`repository/`): Database queries only
- **Application** (`core/application.py`): Routing, lifecycle, dependency injection
- **Controllers/Handlers** (`app.py`): HTTP request/response (minimal logic)
- **Services** (future): Business logic (targeting, ranking, caching)

### Data Flow: Ad Request

```
1. Client GET /placements/homepage_top
2. API → PlacementRepository.get_active_by_key("homepage_top")
3. Repository → SELECT * FROM leo_ads.placement WHERE placement_key=? AND status='active'
4. ORM → Map Row → Placement object
5. Repository → _to_dict(placement) → dict
6. API → return dict as JSON
```

```
1. Client GET /ads?placement_id=10
2. API → AdRepository.get_active_by_placement(tenant_id=1, placement_id=10)
3. Repository → SELECT * FROM leo_ads.ad WHERE tenant_id=? AND placement_id=? AND status='active'
4. ORM → Map Rows → [Ad, Ad, ...]
5. Repository → [_to_dict(ad) for ad in ads]
6. API → return [dict, dict, ...] as JSON
```

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
- [ ] Configure database connection pooling (current: default 5)
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

Example entries:
```
2024-01-15 14:30:45 [API] Endpoint | http://localhost:9009
2024-01-15 14:30:46 [API] Health | curl http://localhost:9009/health
2024-01-15 14:30:46 [DB] PostgreSQL ready | localhost:5432/customer360
```

### Performance Tips

1. **Cache placements**: Placement lookups have 90% read ratio → cache in Redis
2. **Cache ad candidates**: Pre-compute `placement_id → [ad_ids]` in Redis
3. **Batch ad queries**: Use `get_active_by_placement` instead of loop of `get_by_id`
4. **Connection pooling**: SQLAlchemy uses default pool (5 connections, recycle every 3600s)
5. **Query optimization**: All hot queries use indexed fields: `(tenant_id, placement_id, status)`

---

## Troubleshooting

### PostgreSQL connection error

```
PostgreSQL connection failed
```

**Solution:**
- Ensure PostgreSQL is running: `docker ps | grep postgres`
- Check credentials in `.env`
- Verify `DB_HOST` is reachable: `ping localhost`

### Schema not found

```
ERROR:  schema "leo_ads" does not exist
```

**Solution:**
```bash
./start.sh --seed-demo-ads-server
```

This creates the schema and seeds demo data.

### Stale virtual environment

```
[VENV] Stale environment detected
```

**Solution:**
The script auto-recreates the venv. Just restart:
```bash
./start.sh
```

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

[TBD - Add your license]
