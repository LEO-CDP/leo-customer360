#!/usr/bin/env bash
# CI smoke test for docs-vector-search. Byte-compiles every module — py_compile
# does NOT execute imports, so this needs none of the heavy runtime deps
# (fastembed, llama-cpp-python, psycopg) installed. Catches syntax/indentation
# breakage on every branch push; the real integration runs at deploy time (enrich
# against the vDB). Mirrors the ads-server/run_unit_tests.sh convention.
set -euo pipefail
cd "$(dirname "$0")"

echo "== docs-vector-search: py_compile src/*.py =="
python -m py_compile src/*.py
echo "OK — all modules compile."
