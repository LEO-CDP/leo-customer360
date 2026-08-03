#!/usr/bin/env bash
# Single-click PROD deploy (HA Postgres cluster). Runs deploy.sh prod.
# NOTE: applies immediately with -auto-approve (no confirmation prompt).
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy.sh" prod
