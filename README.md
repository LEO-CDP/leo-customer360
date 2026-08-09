
# Customer 360 for Composable CDP

## What is a Composable CDP?

A composable CDP is an approach to building customer data platform capabilities by assembling modular, best-of-breed components — each handling a different stage of the Customer Intelligence Loop (collect → resolve identity → segment → activate) — instead of buying one single, bundled, black-box platform. Most composable CDPs are **warehouse-native**: they run on the cloud data warehouse you already operate (Snowflake, BigQuery, Databricks, or in this case plain PostgreSQL) and add specialized, swappable components on top for identity resolution, segmentation, and activation.


![](./docs/composable-cdp.png)

## What is Customer 360?

Customer 360 is the **identity-resolution and golden-record component** of that composable stack for LEO CDP: a self-hosted PostgreSQL 16 schema (`customer360`) that consolidates customer identity and behavior across channels — AppsFlyer (mobile attribution), MoEngage (engagement), Web Tracking/GA4, POS, and Core Banking — plus a B2B **CRM journey graph** (Lead → Contact → Opportunity), a partitioned **behavioral event fact table**, and two runnable Python services that operate on the schema. It is the piece that would otherwise be an expensive, closed-source SaaS subscription (Segment/mParticle/Amperity-style identity resolution) — here it's transparent SQL and Python you can read, extend, and run on commodity infrastructure.

![](./ui-wireframes/customer-360-profile-details.png)

It ships as three independently runnable pieces:

| Component | Role | Tech |
|---|---|---|
| [`database-schema.sql`](database-init/database-schema.sql) | Single source of truth for the `customer360` schema | PostgreSQL 16, `pgvector`, `postgis`, `uuid-ossp`, `pgcrypto` |
| [`backend-system/identity_resolution/`](backend-system/identity_resolution) | **Customer Identity Resolution (CIR)** engine — links/merges raw profiles into master (golden) profiles | Python + psycopg2, orchestrated by **[Dagster](https://dagster.io)** |
| [`customer360-api/`](customer360-api) | REST API (CRUD + reporting) over the whole schema | FastAPI + SQLAlchemy 2 ORM |

> **Backend pipelines run on Dagster.** [`backend-system/`](backend-system) is a Dagster workspace: `identity_resolution` (above) plus the (in-progress) `scoring`/`segmentation`/`analytics` services each register as a Dagster job, giving them shared scheduling, retries, and one run-history UI instead of separate ad-hoc scripts. Full architecture, why it scales, and how to add a new service: **[`backend-system/README.md`](backend-system/README.md)**.

## Repository core services (full)

| Path | Role |
|---|---|
| [`database-init/`](database-init) | Source of truth for the Postgres schema: [`database-schema.sql`](database-init/database-schema.sql), plus `init-core-database.sql` and `data-view-for-llm.sql` |
| [`backend-system/`](backend-system) | Dagster workspace — `identity_resolution` (CIR, implemented) and `segmentation` (implemented) are real; `scoring`, `analytics`, `data_synch`, `email_engine`, `notification_engine`, `personalization` are placeholder code locations reserved for future services. See [`backend-system/README.md`](backend-system/README.md) |
| [`customer360-api/`](customer360-api) | FastAPI REST API (CRUD + reporting) over the whole schema. See [`customer360-api/customer360-api.md`](customer360-api/customer360-api.md) |
| [`frontend-admin/`](frontend-admin) | FastAPI-served admin SPA (Tailwind CDN + jQuery + Handlebars) that consumes `customer360-api` over AJAX. See [`frontend-admin/README.md`](frontend-admin/README.md) |
| [`all-data-simulator/`](all-data-simulator) | Synthetic raw-data generators (AppsFlyer, GA4) used to seed/UAT-test the ingestion pipeline. See [`all-data-simulator/README.md`](all-data-simulator/README.md) |
| [`docs/`](docs) | Architecture, operations, and planning docs — start with [`TECHNICAL-DOCUMENTATION.md`](docs/TECHNICAL-DOCUMENTATION.md) and [`DOCKER-COMPOSE-GUIDE.md`](docs/DOCKER-COMPOSE-GUIDE.md) |
| [`postgres/`](postgres), [`redis/`](redis) | Custom Dockerfiles for the Postgres (PostGIS + pgvector) and Redis images used by `docker-compose.yml` |
| [`ui-wireframes/`](ui-wireframes) | UI/UX wireframe references for the admin frontend |

## Quick start

**Production-shaped stack (Docker Compose: postgres + redis + keycloak + cir + api):**

```bash
./manage-c360.sh start     # first run creates .env from .env.example and prompts for secrets
./manage-c360.sh status
./manage-c360.sh seed-demo # optional: seed POC/UAT demo data
```

See [`docs/DOCKER-COMPOSE-GUIDE.md`](docs/DOCKER-COMPOSE-GUIDE.md) for the full
operations reference (ports, services, Keycloak realm/client setup).

**Local development (infra in Docker, services run on the host):**

```bash
./dev-c360.sh   # postgres + redis + keycloak + MinIO; auto-seeds demo data if the DB is empty
```

Then run `customer360-api` (`customer360-api/start.sh`) and the CIR worker
(`backend-system/identity_resolution/run-demo.sh`) directly on the host.

**Tests:**

```bash
./run_all_tests.sh   # customer360-api + backend-system/identity_resolution unit tests
```

## Authentication (calling `customer360-api` as a dev engineer)

Every protected endpoint needs `tenant_id`/`user_id` resolved from one of:

- **Dev JWT (recommended, `SSO_LOGIN=false`)** -- log in once, then send the
  token like a real production caller would:
  ```bash
  curl -s -X POST http://localhost:8000/api/v1/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"<DEFAULT_ROOT_PASSWORD from .env>"}'
  # -> {"access_token": "...", "tenant_id": "...", "user_id": "...", ...}

  curl -s http://localhost:8000/api/v1/users/me -H "Authorization: Bearer <access_token>"
  ```
  Or open `http://localhost:8000/docs`, click **Authorize**, and paste the
  `access_token` to call any endpoint from Swagger UI.
- **SSO (`SSO_LOGIN=true`)** -- the same `Authorization: Bearer <token>`
  contract, but the token comes from Keycloak (see `frontend-admin`'s login
  flow, or `POST /api/v1/auth/callback` for a code exchange).
- **Dev headers (`SSO_LOGIN=false`, quick shortcut)** -- `X-Tenant-Id`/
  `X-User-Id` headers, no login required, for endpoints that don't need a
  resolved user profile.

Full reference, endpoint catalog, and auth-expectation matrix:
[`customer360-api/customer360-api.md`](customer360-api/customer360-api.md).

## References

* [LEOCDP.com](https://leocdp.com) 
* Salesforce Customer 360 Graph Model (adapted) — inspiration for the Lead/Campaign/Contact/Account/Opportunity journey graph.
* [LEO CDP 1.0](https://github.com/trieu/leo-cdp) (ArangoDB 3.11-based) — the sibling CDP module in this monorepo; `cdp_event_catalog`'s event vocabulary is adapted from its [`TrackingEvent`](https://github.com/trieu/leo-cdp/blob/master/core-leo-cdp/src/main/java/leotech/cdp/model/analytics/TrackingEvent.java) taxonomy so events stay consistent whether they land in ArangoDB or here in Postgres.
* [pgvector](https://github.com/pgvector/pgvector) / [PostGIS](https://postgis.net/)
* Google Customer Match / Enhanced Conversions (hashed-PII matching pattern)

