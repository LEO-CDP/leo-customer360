#!/usr/bin/env bash
###############################################################################
# Run the SCRUM-93/94 end-to-end suite against a live deployment (UAT).
#
#   ./test.sh                 # run all e2e tests
#   ./test.sh -k idempotent   # extra args pass straight through to pytest
#
# Config is loaded from tests/e2e/.env (git-ignored -- never committed). When
# SSO is on, a FRESH Keycloak token is minted per run via the password grant
# (access tokens expire, so we store creds, not a token). If E2E_BEARER_TOKEN is
# already set (env or .env), minting is skipped.
#
# Required in .env:
#   E2E_BASE_URL            deployment root, e.g. https://beta.leocdp.com/c360api
# SSO-on auth (mint a token) -- set these:
#   E2E_KC_TOKEN_URL        Keycloak token endpoint (realm .../openid-connect/token)
#   E2E_KC_CLIENT_ID        confidential client id (e.g. customer360-api)
#   E2E_KC_CLIENT_SECRET    that client's secret
#   E2E_SSO_USERNAME        user with a tenant-admin realm role (e.g. c360admin)
#   E2E_SSO_PASSWORD        that user's password
# SSO-off auth instead: set E2E_TENANT_ID (+ optional E2E_USER_ID); no token.
# Optional: E2E_TENANT_ID (else derived from the token), E2E_SEGMENT_ID,
#   E2E_SEGMENT_SQL, E2E_API_PREFIX, E2E_VERIFY_TLS, E2E_TIMEOUT.
###############################################################################
set -Eeuo pipefail

# Git Bash (MSYS) rewrites env-var values that look like POSIX paths (e.g.
# E2E_API_PREFIX=/api/v1 -> C:/Program Files/Git/api/v1) when launching a native
# python.exe. Exclude all vars from that conversion so values pass through
# literally. Harmless on Linux/macOS.
export MSYS2_ENV_CONV_EXCL='*'
export MSYS_NO_PATHCONV=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_HOME="$(cd "$HERE/../.." && pwd)"   # customer360-api

# --- 1) config: load .env locally, or use env already set (CI) ---------------
if [ -f "$HERE/.env" ]; then
  set -a; . "$HERE/.env"; set +a       # local dev: git-ignored .env
elif [ -z "${E2E_BASE_URL:-}" ]; then
  echo "!! no $HERE/.env and E2E_BASE_URL not set." >&2
  echo "   Local: create tests/e2e/.env (git-ignored). CI: pass E2E_* via the job env." >&2
  exit 1
fi
: "${E2E_BASE_URL:?E2E_BASE_URL not set (via .env or environment)}"

# --- 2) mint a Keycloak token if none supplied but SSO creds are present -----
if [ -z "${E2E_BEARER_TOKEN:-}" ] && [ -n "${E2E_SSO_USERNAME:-}" ]; then
  : "${E2E_KC_TOKEN_URL:?set E2E_KC_TOKEN_URL to mint a token}"
  : "${E2E_KC_CLIENT_ID:?set E2E_KC_CLIENT_ID to mint a token}"
  echo ">> minting Keycloak token for ${E2E_SSO_USERNAME} ..."
  _tok_json=$(curl -sS -m 30 -X POST "$E2E_KC_TOKEN_URL" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=password" \
    --data-urlencode "client_id=${E2E_KC_CLIENT_ID}" \
    --data-urlencode "client_secret=${E2E_KC_CLIENT_SECRET:-}" \
    --data-urlencode "username=${E2E_SSO_USERNAME}" \
    --data-urlencode "password=${E2E_SSO_PASSWORD:-}" \
    --data-urlencode "scope=openid")
  E2E_BEARER_TOKEN=$(printf '%s' "$_tok_json" \
    | python -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)
  if [ -z "${E2E_BEARER_TOKEN}" ]; then
    echo "!! token mint failed:" >&2
    printf '%s' "$_tok_json" \
      | python -c "import sys,json;d=json.load(sys.stdin);print(d.get('error'),'-',d.get('error_description'))" 2>/dev/null >&2 \
      || printf '%s\n' "$_tok_json" | head -c 300 >&2
    exit 1
  fi
  export E2E_BEARER_TOKEN
  # Derive the tenant from the token's tenant_id claim when not set explicitly.
  if [ -z "${E2E_TENANT_ID:-}" ]; then
    E2E_TENANT_ID=$(printf '%s' "$E2E_BEARER_TOKEN" | python -c "
import sys,base64,json
t=sys.stdin.read().strip().split('.')[1]; t+='='*(-len(t)%4)
print(json.loads(base64.urlsafe_b64decode(t)).get('tenant_id',''))" 2>/dev/null || true)
    export E2E_TENANT_ID
  fi
fi

# --- 3) resolve the venv python (built by ./run_unit_tests.sh) --------------
VENV_PY="$API_HOME/.venv/Scripts/python.exe"          # Windows
[ -x "$VENV_PY" ] || VENV_PY="$API_HOME/.venv/bin/python"   # Linux/macOS
[ -x "$VENV_PY" ] || VENV_PY="$(command -v python || command -v python3)"

echo ">> E2E_BASE_URL=${E2E_BASE_URL}  E2E_TENANT_ID=${E2E_TENANT_ID:-<unset>}  token=$([ -n "${E2E_BEARER_TOKEN:-}" ] && echo set || echo none)"

# Sweeper mode: `CLEANUP=1 ./test.sh` or `./test.sh --cleanup` -- remove residual
# e2e artifacts for the tenant instead of running the suite.
if [ "${1:-}" = "--cleanup" ] || [ "${CLEANUP:-}" = "1" ]; then
  cd "$HERE"                      # relative path avoids MSYS arg-path munging
  exec "$VENV_PY" cleanup.py
fi

# Case selection by TEST_PLAN.md id: `CASES="S94-02 R-A1" ./test.sh` (space-separated,
# prefix ok e.g. CASES=S94). Equivalent to passing `--case S94-02 --case R-A1`.
# You can also pass `--case ...` or any pytest arg (`-k`, `-x`, a nodeid) directly.
CASE_ARGS=()
if [ -n "${CASES:-}" ]; then
  for c in $CASES; do CASE_ARGS+=(--case "$c"); done
fi

cd "$API_HOME"
exec "$VENV_PY" -m pytest tests/e2e -v ${CASE_ARGS[@]+"${CASE_ARGS[@]}"} "$@"
