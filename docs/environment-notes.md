# Environment configuration notes

This repository uses a single root-level environment file, `.env`. The companion file `.env.example` is the canonical template and should stay aligned with the live `.env` file.

## General guidance

- Copy `.env.example` to `.env` before local development.
- Docker Compose overrides `DB_HOST` and `REDIS_HOST` to the internal service names `postgres` and `redis` for containers running on the shared network.
- `SSO_LOGIN=false` is the default for local development. Enable SSO only when Keycloak is configured and reachable.
- `POSTGRES_HOST_BIND`, `REDIS_HOST_BIND`, `C360_API_HOST`, `KEYCLOAK_HOST_BIND`, and `MINIO_HOST_BIND` default to loopback. Change them only if you need access from other machines.

## Database and cache

- `DB_HOST`: PostgreSQL hostname. Default: `localhost`
- `DB_PORT`: PostgreSQL port. Default: `5432`
- `DB_USER`: PostgreSQL superuser name. Default: `postgres`
- `DB_PASSWORD`: PostgreSQL password. Default: `change_me_postgres_password`
- `DB_NAME`: Application database name. Default: `customer360`
- `DB_SCHEMA`: Primary schema for application objects. Default: `customer360`
- `POSTGRES_HOST_PORT`: Host-published PostgreSQL port. Default: `5432`
- `POSTGRES_HOST_BIND`: Bind address for the published PostgreSQL port. Default: `127.0.0.1`
- `REDIS_HOST`: Redis hostname. Default: `localhost`
- `REDIS_PORT`: Redis port. Default: `6580`
- `REDIS_DB`: Redis database number. Default: `0`
- `REDIS_PASSWORD`: Redis password. Default: `change_me_redis_password`
- `REDIS_HOST_PORT`: Host-published Redis port. Default: `6580`
- `REDIS_HOST_BIND`: Bind address for the published Redis port. Default: `127.0.0.1`
- `CACHE_ENABLED`: Enables the response cache layer. Default: `true`
- `CACHE_TTL_SECONDS`: Cache TTL in seconds. Default: `60`

## API and database pool settings

- `DB_POOL_SIZE`: SQLAlchemy pool size. Default: `10`
- `DB_MAX_OVERFLOW`: SQLAlchemy maximum overflow connections. Default: `20`
- `DB_POOL_RECYCLE_SECONDS`: Connection recycle interval. Default: `1800`
- `DB_POOL_PRE_PING`: Enables SQLAlchemy pre-ping for connection health checks. Default: `true`
- `DB_ECHO_SQL`: Enables SQL echo for debugging. Default: `false`
- `C360_API_DEFAULT_PAGE_SIZE`: Default page size for API pagination. Default: `100`
- `C360_API_MAX_PAGE_SIZE`: Maximum page size allowed by the API. Default: `1000`
- `C360_API_HOST`: Host interface for the API server. Default: `0.0.0.0`
- `C360_API_PORT`: Port for the API server. Default: `8008`
- `UVICORN_RELOAD`: Enables auto-reload for the development server. Default: `false`
- `C360_API_PORT`: Host-published API port. Default: `8008`
- `C360_API_HOST`: Bind address for the published API port. Default: `127.0.0.1`

## Frontend admin settings

- `FRONTEND_API_HOSTNAME`: Browser-visible URL for the customer360 API. Default: `http://localhost:8008/c360api`
- `FRONTEND_TENANT_ID`: Tenant identifier used by the admin UI. Default: `11111111-1111-1111-1111-111111111111`
- `FRONTEND_HOST_BIND`: Bind address for the frontend service. Default: `0.0.0.0`
- `FRONTEND_HOST_PORT`: Host-published frontend port. Default: `8890`
- `FRONTEND_UVICORN_RELOAD`: Enables auto-reload for the frontend dev server. Default: `false`

## Identity resolution and background jobs

- `CIR_BATCH_SIZE`: Batch size for identity resolution processing. Default: `5000`
- `CIR_POLL_INTERVAL_SECONDS`: Interval between identity resolution worker polls. Default: `30`
- `DAGSTER_UI_HOST`: Host interface for the Dagster UI. Default: `127.0.0.1`
- `DAGSTER_UI_PORT`: Port for the Dagster UI. Default: `3000`

## Authentication and SSO

- `SSO_LOGIN`: Enables Keycloak-based authentication. Default: `false`
- `DEFAULT_ROOT_USERNAME`: Local bootstrap admin username. Default: `admin`
- `DEFAULT_ROOT_PASSWORD`: Local bootstrap admin password. Default: `change_me_root_password`
- `DEV_JWT_SECRET`: Shared secret used for local JWT issuance when SSO is disabled. Default: `change_me_dev_jwt_secret_min_32_bytes_long`
- `DEV_JWT_EXPIRES_MINUTES`: Token lifetime for local dev JWTs. Default: `480`
- `SSO_LOGIN_URL`: Base URL of the Keycloak server. Default: `http://localhost:8080`
- `KEYCLOAK_REALM`: Keycloak realm name. Default: `leocdp`
- `KEYCLOAK_CLIENT_ID`: Keycloak client ID. Default: `leocdp`
- `KEYCLOAK_CLIENT_SECRET`: Keycloak client secret. Default: `change_me_keycloak_client_secret`
- `KEYCLOAK_CALLBACK_URL`: OAuth callback URL. Default: `http://localhost:8008/auth/callback`
- `KEYCLOAK_VERIFY_SSL`: Whether to verify SSL certificates for Keycloak requests. Default: `false`

## Keycloak container settings

- `KEYCLOAK_ADMIN`: Keycloak admin username. Default: `admin`
- `KEYCLOAK_ADMIN_PASSWORD`: Keycloak admin password. Default: `change_me_keycloak_admin_password`
- `KEYCLOAK_HOST_PORT`: Host-published Keycloak port. Default: `8080`
- `KEYCLOAK_HOST_BIND`: Bind address for the published Keycloak port. Default: `127.0.0.1`
- `KEYCLOAK_VERSION`: Keycloak image tag. Default: `26.7`
- `KEYCLOAK_COMMAND`: Startup command for the Keycloak container. Default: `start-dev`
- `KEYCLOAK_HOSTNAME`: Public hostname advertised by Keycloak. Default: `localhost`

## MinIO (development-only object storage)

- `MINIO_ROOT_USER`: MinIO root username. Default: `change_me_minio_root_user`
- `MINIO_ROOT_PASSWORD`: MinIO root password. Default: `change_me_minio_root_password`
- `MINIO_BUCKET`: Default bucket created for development. Default: `customer360-events-dev`
- `MINIO_API_HOST_PORT`: Host-published MinIO API port. Default: `9000`
- `MINIO_CONSOLE_HOST_PORT`: Host-published MinIO console port. Default: `9001`
- `MINIO_HOST_BIND`: Bind address for the published MinIO ports. Default: `127.0.0.1`

## GenAI settings

- `GOOGLE_GENAI_API_KEY`: API key for the Google GenAI integration. Default: `YOUR_GOOGLE_GENAI_API_KEY`
- `GOOGLE_GENAI_MODEL`: Model identifier for the GenAI integration. Default: `gemini-3.5-flash-lite`
