
# Customer 360

Customer 360 is the identity-resolution and golden-record layer for the LEO CDP platform. The repository combines a PostgreSQL-backed customer graph, a FastAPI read/write API, a Dagster orchestration workspace, and a thin admin frontend that consumes the API.

The code in this repo is not an abstract demo. It reflects a real platform layout with:

- a PostgreSQL 16 schema for master profiles, raw profiles, links, CRM entities, personas, and segmentation metadata
- a FastAPI API in `customer360-api/` for CRUD, reporting, auth, and tenant-scoped access
- a Dagster workspace in `backend-system/` that runs identity resolution, segmentation, and analytics jobs
- a FastAPI ad-serving service in `ads-server/`
- a browser-based admin UI in `frontend-admin/` that calls the API over HTTP
- local Docker-based startup and demo seeding scripts in the repo root

![](./docs/images/composable-cdp-architecture.png)

## What is implemented today

The current repo contains four application services, three active Dagster jobs, and six placeholder Dagster jobs.

| Area | Status | Notes |
|---|---|---|
| `backend-system/identity_resolution/` | Implemented | Dagster identity-resolution job; resolves raw profile matches into master profiles |
| `backend-system/segmentation/` | Implemented | Recomputes active segments and syncs member/tag data back to master profiles |
| `backend-system/analytics/` | Implemented | Hourly Dagster job that aggregates tracking logs and updates source totals |
| `customer360-api/` | Implemented | Main REST API for identity, CRM, persona, reporting, and metadata |
| `data-tracking-api/` | Implemented | Stores immutable tracking events in hourly per-source S3/MinIO objects |
| `ads-server/` | Implemented | Multi-tenant FastAPI ad-serving API with placements, campaigns, creatives, and a browser loader |
| `frontend-admin/` | Implemented | FastAPI shell for the UI, backed by client-side JS and API requests |
| `backend-system/scoring/`, `data_synch/`, `email_engine/`, `notification_engine/`, `campaign_activation/`, `personalization/` | Placeholder | Runnable Dagster scaffolds ready for their service logic |

## Repository structure

| Path | Purpose |
|---|---|
| [`database-init/`](database-init) | Schema source: `database-schema.sql`, seed/init scripts, and SQL views |
| [`backend-system/`](backend-system) | Dagster workspace with nine code locations: identity resolution, segmentation, analytics, and six placeholder services |
| [`customer360-api/`](customer360-api) | FastAPI service with routers, auth, SQLAlchemy models, and business logic |
| [`data-tracking-api/`](data-tracking-api) | FastAPI ingestion service that writes hourly tracking-log objects to S3/MinIO |
| [`ads-server/`](ads-server) | Standalone FastAPI ad-serving service with its own database, cache, and widget code |
| [`frontend-admin/`](frontend-admin) | Thin admin UI served by FastAPI and loaded from static templates |
| [`all-data-simulator/`](all-data-simulator) | Synthetic raw data and optional S3/MinIO upload helpers |
| [`deployments/`](deployments) | Deployment scripts, infrastructure component configuration, and deployment diagrams |
| [`k8s/`](k8s) | Kubernetes deployment documentation and manifests |
| [`postgres/`](postgres), [`redis/`](redis) | Custom Docker image setup for PostgreSQL/PostGIS/pgvector and Redis |
| [`docs/`](docs) | Architecture, operations, and planning material |
| [`ui-wireframes/`](ui-wireframes) | UI design references |
| `docker-compose.yml`, `dev-docker-compose.yml`, `dev-no-sso-docker-compose.yml` | Production-style, local-development, and no-SSO Compose stacks |
| `dev-c360.sh`, `dev-stop-and-delete-all.sh`, `manage-c360.sh`, `run_all_tests.sh` | Local startup, cleanup, stack management, and consolidated test scripts |

## Runtime architecture

The repo runs with the following high-level flow:

1. Postgres stores the canonical `customer360` schema and all operational metadata.
2. `customer360-api` exposes the public API and enforces tenant-aware auth via middleware.
3. The Dagster backend loads nine code locations; identity resolution, segmentation, and analytics are active, while six additional service locations are runnable placeholders.
4. The data-tracking API stores immutable hourly NDJSON batches in per-source S3 buckets; dev uses the in-network MinIO service.
5. The admin frontend calls the API directly, without direct database access.
6. The standalone ads server serves tenant-scoped ad placements and creatives through its API and browser loader.
7. Local dev and production scripts bootstrap dockerized infrastructure and run the platform as a coherent stack.

## Local development quick start

### 1) Prepare environment

```bash
cp .env.example .env
```

Then edit the values in `.env` for your local Postgres, Redis, Keycloak, and host ports. The repo includes a full env template and operational notes in the docs.

### 2) Start the dev stack

```bash
./dev-c360.sh
```

This script starts the infra stack and the MinIO-backed data-tracking API in Docker, then automatically seeds demo data if the database is empty. It is meant for the workflow where Postgres and Redis run in Docker while the main API and backend workers run directly on the host.

### 3) Run host services

In a separate terminal, start the API and backend workers:

```bash
cd customer360-api
./start.sh

cd ../backend-system/identity_resolution
./run-demo.sh
```

The admin frontend can also be started separately:

```bash
cd ../frontend-admin
./start.sh
```

## Production-style stack

For the packaged stack using Docker Compose, run:

```bash
./manage-c360.sh start
./manage-c360.sh status
```

This covers the main production service stack in `docker-compose.yml`, including Postgres, Redis, Keycloak, Dagster, the Customer 360 API, and the data-tracking API. The dev stack in `dev-docker-compose.yml` additionally runs MinIO and its initializer. The standalone `ads-server/` service has its own startup scripts.

## Service entrypoints

The repo uses these primary entrypoints:

- `customer360-api/app.py` — FastAPI API entrypoint
- `data-tracking-api/app.py` — CDP tracking-log FastAPI entrypoint (port 8010)
- `ads-server/app.py` — ad-serving FastAPI entrypoint (port 9009 by default)
- `backend-system/workspace.yaml` — Dagster workspace containing all nine backend code locations
- `backend-system/identity_resolution/worker.py` — legacy local polling helper; production runs the Dagster job
- `frontend-admin/app.py` — admin frontend shell
- `manage-c360.sh` — production-style Docker stack manager
- `dev-c360.sh` — local dev infrastructure bootstrap

## Authentication and tenant context

The API is auth-protected on nearly every route. The current implementation requires a valid bearer token on all non-exempt endpoints, and it resolves tenant/user context from the token or Keycloak-backed auth flow. The code now intentionally rejects header-only login shortcuts, so the runtime behavior matches the current API docs in `customer360-api/customer360-api.md`.

The usual local flow is:

```bash
curl -s -X POST http://localhost:8008/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<password from .env>"}'
```

Then pass the returned `access_token` as a bearer token in subsequent requests.

## Testing

The repo includes a consolidated test runner:

```bash
./run_all_tests.sh
```

This is the current project-level test entrypoint for the Customer 360 API, identity resolution, segmentation, and LEO ad server suites. The data-tracking API also has its own `data-tracking-api/run_unit_tests.sh` runner.

## Key documentation

Start here for deeper context:

- [`docs/TECHNICAL-DOCUMENTATION.md`](docs/TECHNICAL-DOCUMENTATION.md)
- [`docs/DOCKER-COMPOSE-GUIDE.md`](docs/DOCKER-COMPOSE-GUIDE.md)
- [`customer360-api/customer360-api.md`](customer360-api/customer360-api.md)
- [`data-tracking-api/README.md`](data-tracking-api/README.md)
- [`ads-server/README.md`](ads-server/README.md)
- [`backend-system/README.md`](backend-system/README.md)
- [`frontend-admin/README.md`](frontend-admin/README.md)
- [`deployments/README.md`](deployments/README.md)
- [`k8s/README.md`](k8s/README.md)

## References

- [LEOCDP.com](https://leocdp.com)
- [Dagster](https://dagster.io)
- [FastAPI](https://fastapi.tiangolo.com/)
- [PostgreSQL](https://www.postgresql.org/)
- [pgvector](https://github.com/pgvector/pgvector)
- [PostGIS](https://postgis.net/)

