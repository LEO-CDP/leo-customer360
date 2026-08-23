#!/usr/bin/env bash
# =============================================================================
# admin-uat.sh — one-off ADMIN operations on the deployed UAT platform, chosen
# from a FIXED list of supported actions. UAT ONLY (the env is hard-locked).
#
# This is the deployed-platform counterpart of the local dev orchestrator
# dev-c360.sh: dev-c360 drives docker-compose on your laptop; this drives the
# uat vServers over SSH + the deployments/ scripts. Invoked by
# .github/workflows/admin-uat.yml (workflow_dispatch — pick the action from a
# dropdown), or run locally from deployments/.
#
#   ./admin-uat.sh <action>
#
# Supported actions (dev-c360.sh equivalent in parentheses):
#   db-status        Row counts for key CDP tables            (print_database_status)
#   seed-demo        Seed CIR/demo data — idempotent          (seed_demo_if_empty)
#   bootstrap-realm  Re-run idempotent Keycloak realm/roles/client bootstrap
#   restart-apps     Restart api + ads + frontend containers  (restart_host_services)
#   restart-backend  Restart backend-system (Dagster)
#   redeploy-apps    Pull latest images + recreate api/ads/frontend/backend
#   flush-cache      FLUSHDB the Redis cache (needs CONFIRM=flush-cache)
#
# NOTE: intentionally NO destructive data reset (dev-c360's `reset -v` wipes the
# DB) — that must never be a click on uat. flush-cache only clears the fail-open
# cache and is guarded by CONFIRM.
#
# Creds/targets (same sources the deploy scripts use):
#   - SSH key           SSH_KEY (default ~/.ssh/c360-api_ed25519)
#   - vServer IPs       ../server terraform outputs (init'd by the workflow)
#   - DB host/creds     ../postgres outputs + TF_VAR_db_password / ../postgres/terraform.tfvars
#   - Redis creds       TF_VAR_redis_password / ../cache/terraform.tfvars
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"                 # deployments/
ENV="uat"                            # hard-locked; this script is UAT-only
ACTION="${1:-}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=25)
BUSER="${BASTION_USER:-leocdp360}"

tfval() { grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" 2>/dev/null | sed -E 's/.*"([^"]+)".*/\1/' | head -1; }

# Floating IP of a server by its ../server map key (server/*.tf init'd by the caller).
srv_ip() {
  ( cd server && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -json servers 2>/dev/null ) \
    | python3 -c 'import json,sys
d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}
print(next((i.get("floating_ip") for i in (s.get("internal_interfaces") or []) if i.get("floating_ip")), ""))' "$1"
}
run_on() {  # <floating_ip> — remaining args/stdin are the remote command
  local ip="$1"; shift
  [ -n "$ip" ] || { echo "ERROR: could not resolve the target server IP (is ../server terraform init'd?)"; exit 1; }
  ssh "${SSH_OPTS[@]}" "$BUSER@$ip" "$@"
}

echo ">> admin-uat: action='$ACTION' env=$ENV"
case "$ACTION" in
  # --- actions that map cleanly onto existing deploy-all steps ---------------
  seed-demo)       exec bash deploy-all.sh "$ENV" --only seed -y ;;
  bootstrap-realm) exec bash deploy-all.sh "$ENV" --only sso-realm -y ;;
  redeploy-apps)   exec bash deploy-all.sh "$ENV" --only api,ads,frontend,backend -y ;;

  # --- lightweight container restarts (no image pull) -----------------------
  restart-apps)
    echo ">> Restarting api/ads/frontend on the api box ..."
    run_on "$(srv_ip api)" 'sudo docker restart customer360-api customer360-ads customer360-frontend >/dev/null && sudo docker ps --filter name=customer360- --format "   {{.Names}} {{.Status}}"'
    ;;
  restart-backend)
    echo ">> Restarting backend-system (Dagster) on the backend box ..."
    run_on "$(srv_ip 1x2)" 'sudo docker restart backend-system >/dev/null && sudo docker ps --filter name=backend-system --format "   {{.Names}} {{.Status}}"'
    ;;

  # --- read-only DB status (catalog live-row estimates: RLS-immune) ----------
  db-status)
    DB_HOST="$( (cd postgres && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -raw db_host 2>/dev/null) || true )"
    DB_PORT="$( (cd postgres && terraform output -raw db_port 2>/dev/null) || echo 5432 )"
    DB_NAME="$(tfval db_name "postgres/overlays/$ENV.tfvars")"; DB_NAME="${DB_NAME:-customer360}"
    DB_USER="$(tfval db_username "postgres/overlays/$ENV.tfvars")"; DB_USER="${DB_USER:-app_admin}"
    DB_PASS="${TF_VAR_db_password:-$(tfval db_password postgres/terraform.tfvars)}"
    : "${DB_HOST:?could not read db_host from ../postgres outputs}"; : "${DB_PASS:?missing db_password}"
    echo ">> DB status: $DB_NAME @ $DB_HOST:$DB_PORT (catalog estimates — accurate under RLS):"
    PW_B64="$(printf %s "$DB_PASS" | base64 | tr -d '\n')"
    run_on "$(srv_ip api)" 'bash -s' "$PW_B64" "$DB_HOST" "$DB_PORT" "$DB_USER" "$DB_NAME" <<'RSQL'
set -eu
PW="$(printf %s "$1" | base64 -d)"; H="$2"; P="$3"; U="$4"; D="$5"
command -v psql >/dev/null 2>&1 || { sudo apt-get update -qq; sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-client >/dev/null; }
PGPASSWORD="$PW" psql -h "$H" -p "$P" -U "$U" -d "$D" -v ON_ERROR_STOP=1 -P pager=off <<'SQL'
\echo -- key CDP tables (est. live rows) --
SELECT relname AS table, n_live_tup AS est_rows
FROM pg_stat_user_tables
WHERE schemaname='customer360'
  AND relname IN ('cdp_master_profiles','cdp_content_items','cdp_persona_features',
                  'cdp_raw_events_default','crm_campaign_performance_daily')
ORDER BY relname;
\echo -- schema totals --
SELECT count(*) AS tables, COALESCE(sum(n_live_tup),0) AS est_total_rows
FROM pg_stat_user_tables WHERE schemaname='customer360';
SQL
RSQL
    ;;

  # --- flush the Redis response cache (fail-open; guarded) -------------------
  flush-cache)
    [ "${CONFIRM:-}" = "flush-cache" ] || { echo "ERROR: flush-cache clears the Redis cache — re-run with CONFIRM=flush-cache (the workflow's 'confirm' input)."; exit 1; }
    REDIS_PORT="$(tfval redis_port "cache/overlays/$ENV.tfvars")"; REDIS_PORT="${REDIS_PORT:-6580}"
    REDIS_PASS="${TF_VAR_redis_password:-$(tfval redis_password cache/terraform.tfvars)}"
    : "${REDIS_PASS:?missing redis_password}"
    echo ">> Flushing the Redis cache (FLUSHDB) on the api box ..."
    PW_B64="$(printf %s "$REDIS_PASS" | base64 | tr -d '\n')"
    run_on "$(srv_ip api)" 'bash -s' "$PW_B64" "$REDIS_PORT" <<'RC'
set -eu
PW="$(printf %s "$1" | base64 -d)"; PORT="$2"
b="$(sudo docker exec c360-redis redis-cli -p "$PORT" -a "$PW" --no-auth-warning DBSIZE 2>/dev/null || echo '?')"
sudo docker exec c360-redis redis-cli -p "$PORT" -a "$PW" --no-auth-warning FLUSHDB >/dev/null
a="$(sudo docker exec c360-redis redis-cli -p "$PORT" -a "$PW" --no-auth-warning DBSIZE 2>/dev/null || echo '?')"
echo "   DBSIZE before=$b after=$a (fail-open cache — repopulates on demand)"
RC
    ;;

  *)
    echo "ERROR: unknown/empty action '$ACTION'."
    echo "Supported: db-status | seed-demo | bootstrap-realm | restart-apps | restart-backend | redeploy-apps | flush-cache"
    exit 1
    ;;
esac
