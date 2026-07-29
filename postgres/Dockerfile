# PostgreSQL 16 with PostGIS + pgvector + contrib extensions (uuid-ossp,
# pgcrypto, pg_trgm, fuzzystrmatch) needed by core-customer360/database-schema.sql
# and by backend-system/identity_resolution's fuzzy-matching rules.
#
# Build context is the core-customer360/ directory (see docker-compose.yml)
# so this Dockerfile can COPY the schema and init bootstrap SQL files.
FROM postgis/postgis:16-3.5

# Prevent apt-get from throwing interactive prompts during build
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Scripts in /docker-entrypoint-initdb.d/ run once, in filename order, ONLY
# when the data directory is empty (first-ever container start). See:
# https://hub.docker.com/_/postgres -> "Initialization scripts".
COPY postgres/init/00-extensions.sql /docker-entrypoint-initdb.d/00-extensions.sql
COPY database-init/database-schema.sql /docker-entrypoint-initdb.d/01-schema.sql
COPY database-init/init-core-database.sql /docker-entrypoint-initdb.d/02-init-core-database.sql

# IMPROVEMENT: Increased start-period to 120s to allow large schema scripts to finish executing.
# IMPROVEMENT: Added default fallbacks (:-postgres) so the healthcheck doesn't crash if vars are unset.
HEALTHCHECK --interval=15s --timeout=10s --start-period=120s --retries=5 \
    CMD pg_isready -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-postgres}" || exit 1