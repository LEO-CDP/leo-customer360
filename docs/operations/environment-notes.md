# Environment configuration notes

The root `.env` file is the shared configuration for the Customer 360
services. Use `.env.example` as its template. The `ads-server` service is
independent and uses `ads-server/.env` from `ads-server/.env.example`.

## General guidance

- Copy `.env.example` to `.env` before local development.
- Compose overrides `DB_HOST`, `DB_PORT`, `REDIS_HOST`, and `REDIS_PORT` inside containers.
- `*_HOST_BIND` controls the host interface for a published port.
- `*_HOST_PORT` controls the host port for a published service.
- Published services default to loopback. Change the bind address only when needed.
- `SSO_LOGIN=false` keeps local authentication enabled without Keycloak.

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
- `API_DEFAULT_PAGE_SIZE`: Default API page size. Default: `100`
- `API_MAX_PAGE_SIZE`: Maximum API page size. Default: `1000`
- `C360_API_HOST`: Host bind address for the published API port. Default: `127.0.0.1`
- `C360_API_PORT`: Host-published API port. Default: `8008`
- `UVICORN_RELOAD`: Enables auto-reload for the development server. Default: `false`

## Frontend admin settings

- `FRONTEND_API_HOSTNAME`: Browser-visible API base URL. Default: `https://c360.example.com/c360api`
- `FRONTEND_TENANT_ID`: Tenant identifier used by the admin UI. Default: `11111111-1111-1111-1111-111111111111`
- `FRONTEND_HOST_BIND`: Host bind address for the frontend port. Default: `127.0.0.1`
- `FRONTEND_HOST_PORT`: Host-published frontend port. Default: `8890`
- `FRONTEND_UVICORN_RELOAD`: Enables auto-reload for the frontend dev server. Default: `false`
- `FRONTEND_ROOT_PATH`: URL prefix for the frontend. Default: `/c360`
- `LEO_OBSERVER_LOG_DOMAIN`: Observer log domain. Default: `c360.example.com`
- `LEO_OBSERVER_TRACKING_URI`: Observer tracking path. Default: `/data/api/v1/tracking/logs`
- `LEO_OBSERVER_TRACKING_ENDPOINT`: Full observer tracking URL. Default: `https://c360.example.com/data/api/v1/tracking/logs`
- `LEO_OBSERVER_CDN_JS`: Observer SDK URL.

## Identity resolution and background jobs

- `CIR_BATCH_SIZE`: Batch size for identity resolution processing. Default: `5000`
- `CIR_POLL_INTERVAL_SECONDS`: Interval between identity resolution worker polls. Default: `30`
- `ANALYTICS_DATA_SOURCE_LIMIT`: Data sources processed per analytics run. Default: `10`
- `ANALYTICS_LOCK_TTL_SECONDS`: Analytics Redis lock lifetime. Default: `3600`
- `DAGSTER_UI_HOST`: Host interface for the Dagster UI. Default: `127.0.0.1`
- `DAGSTER_UI_PORT`: Port for the Dagster UI. Default: `3000`

## Authentication and SSO

- `SSO_LOGIN`: Enables Keycloak-based authentication. Default: `false`
- `DEFAULT_ROOT_USERNAME`: Local bootstrap admin username. Default: `admin`
- `DEFAULT_ROOT_PASSWORD`: Local bootstrap admin password. Default: `change_me_root_password`
- `DEV_JWT_SECRET`: Shared secret used for local JWT issuance when SSO is disabled. Default: `change_me_dev_jwt_secret_min_32_bytes_long`
- `DEV_JWT_EXPIRES_MINUTES`: Token lifetime for local dev JWTs. Default: `480`
- `C360_AUTH_RATE_LIMIT_MAX_ATTEMPTS`: Failed login attempts per window. Default: `10`
- `C360_AUTH_RATE_LIMIT_WINDOW_SECONDS`: Login rate-limit window. Default: `60`
- `SSO_LOGIN_URL`: Base URL of the Keycloak server. Default: `https://c360.example.com/auth`
- `KEYCLOAK_REALM`: Keycloak realm name. Default: `leocdp`
- `KEYCLOAK_CLIENT_ID`: Keycloak client ID. Default: `leocdp`
- `KEYCLOAK_CLIENT_SECRET`: Keycloak client secret. Default: `change_me_keycloak_client_secret`
- `KEYCLOAK_CALLBACK_URL`: OAuth callback URL. Default: `https://c360.example.com/`
- `KEYCLOAK_VERIFY_SSL`: Whether to verify SSL certificates for Keycloak requests. Default: `false`

## Keycloak container settings

- `KEYCLOAK_ADMIN`: Keycloak admin username. Default: `admin`
- `KEYCLOAK_ADMIN_PASSWORD`: Keycloak admin password. Default: `change_me_keycloak_admin_password`
- `KEYCLOAK_HOST_PORT`: Host-published Keycloak port. Default: `8080`
- `KEYCLOAK_HOST_BIND`: Bind address for the published Keycloak port. Default: `127.0.0.1`
- `KEYCLOAK_VERSION`: Keycloak image tag. Default: `26.7`
- `KEYCLOAK_COMMAND`: Startup command for the Keycloak container. Default: `start-dev`
- `KEYCLOAK_HOSTNAME`: Public hostname advertised by Keycloak. Default: `https://c360.example.com/auth`

## MinIO (development-only object storage)

- `MINIO_ROOT_USER`: MinIO root username. Default: `change_me_minio_root_user`
- `MINIO_ROOT_PASSWORD`: MinIO root password. Default: `change_me_minio_root_password`
- `MINIO_BUCKET`: Default bucket created for development. Default: `customer360-events-dev`
- `MINIO_API_HOST_PORT`: Host-published MinIO API port. Default: `9000`
- `MINIO_CONSOLE_HOST_PORT`: Host-published MinIO console port. Default: `9001`
- `MINIO_HOST_BIND`: Bind address for the published MinIO ports. Default: `127.0.0.1`

## CDP data tracking object storage

- `C360_TRACKING_API_HOST`: Bind address for the tracking API. Default: `127.0.0.1`
- `C360_TRACKING_API_PORT`: Host-published tracking API port. Default: `8010`
- `TRACKING_MAX_EVENTS_PER_REQUEST`: Maximum events accepted in one batch. Default: `1000`
- `OBJECT_STORAGE_MODE`: `s3` for production or `minio` for a host-run dev service.
- `S3_ENDPOINT_URL`: S3-compatible endpoint; dev Compose sets this to `http://minio:9000`.
- `S3_REGION`: AWS region. Default: `us-east-1`
- `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY`: Optional explicit credentials; omit them in AWS when using IAM roles.
- `S3_FORCE_PATH_STYLE`: Required for MinIO. Default: `false`
- `S3_AUTO_CREATE_BUCKETS`: Create `data-tracking-[data_source_id]` on first write. Default: `true`
- `TRACKING_REDIS_KEY_PREFIX`: Prefix for tracking session and rate-limit keys. Default: `data-tracking-api`
- `TRACKING_SESSION_TTL_SECONDS`: Session metadata TTL. Default: `86400`
- `TRACKING_RATE_LIMIT_REQUESTS` / `TRACKING_RATE_LIMIT_WINDOW_SECONDS`: Per-IP request window. Defaults: `1000` / `360`
- `TRACKING_RATE_LIMIT_FAIL_OPEN`: Allow ingestion when Redis rate limiting is unavailable. Default: `true`; use `false` for strict enforcement.
- `TRACKING_BOT_FILTER_ENABLED`: Enable configured user-agent filtering. Default: `true`
- `TRACKING_BOT_USER_AGENT_PATTERNS`: Comma-separated case-insensitive user-agent substrings to discard.

## GenAI settings

- `LEO_GOOGLE_GENAI_API_KEY`: Google GenAI key. Default: `YOUR_GOOGLE_GENAI_API_KEY`
- `LEO_GOOGLE_GENAI_MODEL`: Google GenAI model. Default: `gemini-3.5-flash-lite`
- `LEO_OPENAI_API_KEY`: OpenAI key. Default: `YOUR_OPENAI_API_KEY`
- `LEO_OPENAI_MODEL_NAME`: OpenAI model. Default: `gpt-5.6-luna`
- `LEO_OPENAI_BASE_URL`: OpenAI-compatible API URL. Default: `YOUR_OPENAI_BASE_URL`

## Independent ads-server settings

These keys belong only to `ads-server/.env`. They are not part of the root
global `.env` file.

### Ad server API

- `LEO_AD_API_HOST`: API listen address. Default: `localhost`
- `LEO_AD_API_PORT`: API listen port. Default: `9009`
- `LEO_AD_TRACKING_BASE_URL`: Customer 360 tracking API URL. Default: `http://localhost:8010`

### Ad server PostgreSQL

- `LEO_AD_DB_HOST`: PostgreSQL host. Default: `localhost`
- `LEO_AD_DB_PORT`: PostgreSQL port. Default: `5432`
- `LEO_AD_DB_USER`: PostgreSQL user. Default: `postgres`
- `LEO_AD_DB_PASSWORD`: PostgreSQL password. Set a private value.
- `LEO_AD_DB_NAME`: Database name. Default: `customer360`
- `LEO_AD_DB_SCHEMA`: Ads schema. Default: `leo_ads`

### Ad server Redis

- `LEO_AD_REDIS_HOST`: Redis host. Default: `localhost`
- `LEO_AD_REDIS_PORT`: Redis port. Default: `6580`
- `LEO_AD_REDIS_DB`: Redis database number. Default: `0`
- `LEO_AD_REDIS_PASSWORD`: Redis password. Set a private value.
