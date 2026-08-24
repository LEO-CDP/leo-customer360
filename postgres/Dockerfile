# syntax=docker/dockerfile:1.7

# PostgreSQL 16 with PostGIS + pgvector + contrib extensions (uuid-ossp,
# pgcrypto, pg_trgm, fuzzystrmatch) needed by core-customer360/database-schema.sql
# and by backend-system/identity_resolution's fuzzy-matching rules.
#
# Build context is the core-customer360/ directory (see docker-compose.yml)
# so this Dockerfile can COPY the schema and init bootstrap SQL files.
FROM postgis/postgis:16-3.5

# Prevent apt-get from throwing interactive prompts during build
ARG DEBIAN_FRONTEND=noninteractive

# Force HTTPS for Debian + PGDG apt sources and make apt more resilient to
# transient network failures during image builds.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    set -eux; \
        find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -print0 \
            | xargs -0 sed -i 's|http://|https://|g'; \
        printf '%s\n' \
            'Acquire::Retries "8";' \
            'Acquire::http::Timeout "30";' \
            'Acquire::https::Timeout "30";' \
            'Acquire::ForceIPv4 "true";' \
            > /etc/apt/apt.conf.d/80-retries-and-timeouts; \
        apt-get update \
        && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Scripts in /docker-entrypoint-initdb.d/ run once, in filename order, ONLY
# when the data directory is empty (first-ever container start). See:
# https://hub.docker.com/_/postgres -> "Initialization scripts".
#
# Only the extensions are bootstrapped here (they need superuser and rarely
# change). The SCHEMA and SEED DATA are NO LONGER copied here -- they are owned
# by dbmate migrations (database-init/db/migrations/*.sql), applied by the
# one-shot `migrate` service in docker-compose.yml. This is deliberate: the
# initdb.d hook runs only on an empty volume, so it silently ignored schema
# changes on existing databases. dbmate applies them everywhere, in order.
COPY postgres/init/00-extensions.sql /docker-entrypoint-initdb.d/00-extensions.sql

# IMPROVEMENT: Increased start-period to 120s to allow large schema scripts to finish executing.
# IMPROVEMENT: Added default fallbacks (:-postgres) so the healthcheck doesn't crash if vars are unset.
HEALTHCHECK --interval=15s --timeout=10s --start-period=120s --retries=5 \
    CMD pg_isready -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-postgres}" || exit 1