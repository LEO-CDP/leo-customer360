# syntax=docker/dockerfile:1.7

# PostgreSQL 16 on Debian Bookworm with PostGIS + pgvector. Use the same
# supported PGDG distribution for PostgreSQL and its extension packages.
# PostgreSQL contrib extensions (uuid-ossp, pgcrypto, pg_trgm, fuzzystrmatch)
# are provided by PostgreSQL 16 and enabled in postgres/init/00-extensions.sql.
#
# Build context is the core-customer360/ directory (see docker-compose.yml)
# so this Dockerfile can COPY the schema and init bootstrap SQL files.
FROM postgres:16-bookworm

# Prevent apt-get from throwing interactive prompts during build
ARG DEBIAN_FRONTEND=noninteractive

# Bootstrap CA certificates from the base image's HTTP sources before switching
# Debian and PGDG to HTTPS; make apt resilient to transient build-network failures.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    set -eux; \
        printf '%s\n' \
            'Acquire::Retries "8";' \
            'Acquire::http::Timeout "30";' \
            'Acquire::https::Timeout "30";' \
            'Acquire::ForceIPv4 "true";' \
            > /etc/apt/apt.conf.d/80-retries-and-timeouts; \
        apt-get update; \
        apt-get install -y --no-install-recommends ca-certificates; \
        find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -print0 \
            | xargs -0 sed -i 's|http://|https://|g'; \
        apt-get update \
        && apt-get install -y --no-install-recommends \
            postgresql-16-postgis-3 \
            postgresql-16-postgis-3-scripts \
            postgresql-16-pgvector \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Scripts in /docker-entrypoint-initdb.d/ run once, in filename order, ONLY
# when the data directory is empty (first-ever container start). See:
# https://hub.docker.com/_/postgres -> "Initialization scripts".
COPY postgres/init/00-extensions.sql /docker-entrypoint-initdb.d/00-extensions.sql
COPY customer360-database/database-schema.sql /docker-entrypoint-initdb.d/01-schema.sql
COPY customer360-database/init-cdp-ai-agents.sql /docker-entrypoint-initdb.d/02-init-cdp-ai-agents.sql
COPY customer360-database/init-core-database.sql /docker-entrypoint-initdb.d/03-init-core-database.sql

# IMPROVEMENT: Increased start-period to 120s to allow large schema scripts to finish executing.
# IMPROVEMENT: Added default fallbacks (:-postgres) so the healthcheck doesn't crash if vars are unset.
HEALTHCHECK --interval=15s --timeout=10s --start-period=120s --retries=5 \
    CMD pg_isready -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-postgres}" || exit 1