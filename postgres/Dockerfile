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
        && apt-get install -y --no-install-recommends \
            postgresql-16-pgvector \
            pgbackrest \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# pgbackrest (installed above) powers the physical/PITR backup track: the server
# calls it from archive_command, and a sidecar runs base backups. See
# postgres/docs/04-pgbackrest-pitr.md. Pre-create its spool/log dirs owned by
# the postgres runtime user so archive-async + logging work out of the box.
RUN install -d -o postgres -g postgres /var/spool/pgbackrest /var/log/pgbackrest

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