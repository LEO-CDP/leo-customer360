#!/bin/sh
set -eu

attempt=0
until pg_isready -q -h "$PGHOST" -p "$PGPORT" -U "$PGUSER"; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
        echo "PostgreSQL did not become reachable for schema upgrades." >&2
        exit 1
    fi
    sleep 2
done

psql -X -v ON_ERROR_STOP=1 -f /schema/customer360/legacy-upgrade.sql
