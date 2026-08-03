#!/usr/bin/env bash
# Single-click DEV deploy (standalone Postgres). Runs deploy.sh dev.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy.sh" dev
