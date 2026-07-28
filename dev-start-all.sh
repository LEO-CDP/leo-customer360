
#!/bin/bash
# =============================================================================
# Customer 360 Platform - local DEV bootstrap (infra-only)
#
# Starts the infra-only stack in dev-docker-compose.yml (postgres + redis +
# keycloak) so customer360-api and backend-system/identity_resolution (CIR) can be
# run directly on the host against dockerized Postgres/Redis -- see
# customer360-api/start.sh and backend-system/identity_resolution/run-demo.sh, and
# "non-Docker local dev workflow" in DOCKER-COMPOSE-GUIDE.md section 10.
#
# What it does, in order:
#   1. Ensures '.env' exists (created from '.env.example' if missing) and
#      contains every key currently in '.env.example'.
#   2. Starts (or resets) postgres/redis/keycloak/minio via
#      `docker compose -f dev-docker-compose.yml`.
#   3. Waits for postgres/redis/keycloak/minio containers to report healthy,
#      then waits for the one-shot `minio-init` bucket-bootstrap job to
#      complete.
#   4. Checks whether the Keycloak 'leocdp' realm exists yet; there is no
#      automated realm/client seed script in this repo, so it prints manual
#      setup instructions (DOCKER-COMPOSE-GUIDE.md section 9) when missing.
#   5. Checks whether core demo tables are empty; if empty, runs the
#      seed-demo workflow via backend-system/identity_resolution/run-demo.sh.
#      If not empty, prints current DB row-count status for key tables.
#
# Usage:
#   ./dev-start-all.sh              Start/create services, sync .env, run
#                                    seed-demo only when DB is empty; otherwise
#                                    print DB status counts.
#   ./dev-start-all.sh --no-seed    Same, but skip the CIR demo data seed step.
#   ./dev-start-all.sh reset        DESTRUCTIVE: `docker compose down -v`
#                                    (drops the postgres/redis/minio volumes
#                                    -- this also wipes Keycloak's
#                                    db_keycloak and the MinIO dev bucket)
#                                    then starts fresh and reseeds.
#   ./dev-start-all.sh reset -y     Same as 'reset' but skips the confirmation
#                                    prompt (CI / automation).
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_FILE="dev-docker-compose.yml"
ENV_FILE=".env"
ENV_EXAMPLE_FILE=".env.example"
CIR_DIR="backend-system/identity_resolution"
POSTGRES_CONTAINER="customer360-postgres"
REDIS_CONTAINER="customer360-redis"
KEYCLOAK_CONTAINER="customer360-keycloak"
MINIO_CONTAINER="customer360-minio"
MINIO_INIT_CONTAINER="customer360-minio-init"

# --- Parse args (order-independent) ---
ACTION="up"
SKIP_CONFIRM="false"
SKIP_SEED="false"
for arg in "$@"; do
  case "$arg" in
    reset) ACTION="reset" ;;
    -y|--yes) SKIP_CONFIRM="true" ;;
    --no-seed) SKIP_SEED="true" ;;
    -h|--help)
      sed -n '2,38p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "❌ Unknown argument: $arg (use -h for usage)" >&2
      exit 1
      ;;
  esac
done

# --- docker compose v2 required (depends_on: condition: service_healthy) ---
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  echo "⚠️  Warning: falling back to legacy 'docker-compose' v1 -- 'depends_on: condition: service_healthy' requires Compose v2 (the 'docker compose' plugin)." >&2
  DC=(docker-compose)
else
  echo "❌ Error: neither 'docker compose' (v2 plugin) nor 'docker-compose' found on PATH." >&2
  exit 1
fi
DC_CMD=("${DC[@]}" -f "$COMPOSE_FILE")

# =============================================================================
# 1) .env bootstrap: create from .env.example if missing, then add any keys
#    present in .env.example but missing from .env (without touching values
#    the user already customized).
# =============================================================================
ensure_env_file() {
  if [ ! -f "$ENV_FILE" ]; then
    if [ ! -f "$ENV_EXAMPLE_FILE" ]; then
      echo "❌ Error: neither '${ENV_FILE}' nor '${ENV_EXAMPLE_FILE}' found in ${SCRIPT_DIR}." >&2
      exit 1
    fi
    echo "📄 '${ENV_FILE}' not found -- creating it from '${ENV_EXAMPLE_FILE}'..."
    cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
    echo "⚠️  Edit '${ENV_FILE}' and set real values for DB_PASSWORD, REDIS_PASSWORD, KEYCLOAK_ADMIN_PASSWORD (and KEYCLOAK_CLIENT_SECRET once the client exists -- see DOCKER-COMPOSE-GUIDE.md section 9)."
  fi
}

sync_env_keys() {
  local added=0
  local key line
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *=* ]] && continue
    key="${line%%=*}"
    [ -z "$key" ] && continue
    if ! grep -qE "^${key}=" "$ENV_FILE"; then
      if [ "$added" -eq 0 ]; then
        {
          echo ""
          echo "# --- Added by dev-start-all.sh on $(date +%Y-%m-%d) from ${ENV_EXAMPLE_FILE} ---"
        } >> "$ENV_FILE"
      fi
      echo "$line" >> "$ENV_FILE"
      echo "➕ Added missing key '${key}' to '${ENV_FILE}' (review its value)."
      added=$((added + 1))
    fi
  done < "$ENV_EXAMPLE_FILE"
  if [ "$added" -gt 0 ]; then
    echo "⚠️  ${added} new key(s) added to '${ENV_FILE}' with default/placeholder values -- review before relying on them."
  fi
}

echo "🔧 Checking '${ENV_FILE}'..."
ensure_env_file
sync_env_keys
# shellcheck disable=SC1091
set -a
source "$ENV_FILE"
set +a

# DB_PORT/REDIS_PORT are what host-run apps (customer360-api/start.sh,
# backend-system/identity_resolution/run-demo.sh) connect through; *_HOST_PORT is
# what docker-compose publishes. They must match when running against the
# dockerized services from the host.
if [ "${DB_PORT:-5432}" != "${POSTGRES_HOST_PORT:-5432}" ]; then
  echo "⚠️  DB_PORT (${DB_PORT:-5432}) != POSTGRES_HOST_PORT (${POSTGRES_HOST_PORT:-5432}) in '${ENV_FILE}' -- host-run apps connecting via DB_PORT may not reach the published Postgres port."
fi
if [ "${REDIS_PORT:-6379}" != "${REDIS_HOST_PORT:-6379}" ]; then
  echo "⚠️  REDIS_PORT (${REDIS_PORT:-6379}) != REDIS_HOST_PORT (${REDIS_HOST_PORT:-6379}) in '${ENV_FILE}' -- host-run apps connecting via REDIS_PORT may not reach the published Redis port."
fi

# =============================================================================
# 2) Start / reset postgres + redis + keycloak
# =============================================================================
if [ "$ACTION" = "reset" ]; then
  echo "⚠️  This will run '${DC[*]} -f ${COMPOSE_FILE} down -v', PERMANENTLY DELETING the customer360-pgdata, customer360-redisdata and customer360-miniodata volumes (all Postgres + Redis + MinIO data, including Keycloak's db_keycloak)."
  if [ "$SKIP_CONFIRM" != "true" ]; then
    read -r -p "Type 'yes' to confirm: " CONFIRM_ANSWER
    if [ "$CONFIRM_ANSWER" != "yes" ]; then
      echo "❌ Aborted. No changes made."
      exit 1
    fi
  fi
  echo "🗑️  Tearing down existing containers + volumes..."
  "${DC_CMD[@]}" down -v
fi

echo "🚀 Starting postgres + redis + keycloak + minio (${COMPOSE_FILE})..."
"${DC_CMD[@]}" up -d --build

# =============================================================================
# 3) Wait for the healthchecked services, then for the one-shot minio-init
#    bucket-bootstrap job to finish.
# =============================================================================
wait_for_healthy() {
  local container="$1"
  local max_attempts=30
  local attempt=1
  echo "⏳ Waiting for '${container}' to become healthy..."
  until [ "$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null)" = "healthy" ]; do
    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "❌ Error: '${container}' did not become healthy after ${max_attempts} attempts." >&2
      "${DC_CMD[@]}" logs --tail=50 "$container" || true
      exit 1
    fi
    sleep 2
    attempt=$((attempt + 1))
  done
  echo "🟢 '${container}' is healthy."
}

wait_for_completed() {
  local container="$1"
  local max_attempts=30
  local attempt=1
  echo "⏳ Waiting for '${container}' to finish..."
  until [ "$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null)" = "exited" ]; do
    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "❌ Error: '${container}' did not finish after ${max_attempts} attempts." >&2
      "${DC_CMD[@]}" logs --tail=50 "$container" || true
      exit 1
    fi
    sleep 2
    attempt=$((attempt + 1))
  done
  local exit_code
  exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$container" 2>/dev/null || echo "1")"
  if [ "$exit_code" != "0" ]; then
    echo "❌ Error: '${container}' exited with code ${exit_code}." >&2
    "${DC_CMD[@]}" logs --tail=50 "$container" || true
    exit 1
  fi
  echo "🟢 '${container}' completed successfully."
}

wait_for_healthy "$POSTGRES_CONTAINER"
wait_for_healthy "$REDIS_CONTAINER"
wait_for_healthy "$KEYCLOAK_CONTAINER"
wait_for_healthy "$MINIO_CONTAINER"
wait_for_completed "$MINIO_INIT_CONTAINER"

# =============================================================================
# 4) Keycloak realm check -- no automated realm/client seed script exists in
#    this repo (see DOCKER-COMPOSE-GUIDE.md section 9), so just detect and
#    point at the manual steps instead of pretending to seed it.
# =============================================================================
check_keycloak_realm() {
  local realm="${KEYCLOAK_REALM:-leocdp}"
  echo "🔎 Checking whether Keycloak realm '${realm}' exists..."
  local exists
  exists="$(docker exec -u postgres "$POSTGRES_CONTAINER" psql -U "${DB_USER:-postgres}" -d db_keycloak -tAc \
    "SELECT 1 FROM realm WHERE name = '${realm}'" 2>/dev/null || true)"
  if [ "$exists" != "1" ]; then
    cat <<EOF
⚠️  Keycloak realm '${realm}' not found in 'db_keycloak'. There is no
   automated realm/client seed script in this repo -- create it manually:
     1. Open http://localhost:${KEYCLOAK_HOST_PORT:-8080} and log in with
        KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD from '${ENV_FILE}'.
     2. Follow DOCKER-COMPOSE-GUIDE.md section 9 to create the '${realm}'
        realm, the '${KEYCLOAK_CLIENT_ID:-leocdp}' confidential client, and a
        test user, then copy the client secret into KEYCLOAK_CLIENT_SECRET.
EOF
  else
    echo "🟢 Keycloak realm '${realm}' already exists."
  fi
}
check_keycloak_realm

# =============================================================================
# 5) Check DB status, seed demo data if empty, otherwise print DB status
# =============================================================================
print_database_status() {
  local db_name="${DB_NAME:-customer360}"
  local db_schema="${DB_SCHEMA:-customer360}"
  echo "📊 Database status (${db_schema}):"
  docker exec -u postgres "$POSTGRES_CONTAINER" psql -U "${DB_USER:-postgres}" -d "$db_name" -P pager=off -c \
    "SELECT 'cdp_master_profiles' AS table_name, COUNT(*) AS row_count FROM ${db_schema}.cdp_master_profiles
     UNION ALL
     SELECT 'cdp_raw_events', COUNT(*) FROM ${db_schema}.cdp_raw_events
     UNION ALL
     SELECT 'cdp_content_items', COUNT(*) FROM ${db_schema}.cdp_content_items
     UNION ALL
     SELECT 'crm_transactions', COUNT(*) FROM ${db_schema}.crm_transactions;"
}

seed_demo_if_empty() {
  local db_name="${DB_NAME:-customer360}"
  local db_schema="${DB_SCHEMA:-customer360}"

  echo "🔎 Checking whether demo tables are empty..."
  local status_line
  status_line="$(docker exec -u postgres "$POSTGRES_CONTAINER" psql -U "${DB_USER:-postgres}" -d "$db_name" -tAc \
    "SELECT
       CASE WHEN
         (SELECT COUNT(*) FROM ${db_schema}.cdp_master_profiles) = 0
         AND (SELECT COUNT(*) FROM ${db_schema}.cdp_raw_events) = 0
         AND (SELECT COUNT(*) FROM ${db_schema}.cdp_content_items) = 0
       THEN 'empty' ELSE 'not_empty' END" 2>/dev/null || true)"

  if [ -z "$status_line" ]; then
    echo "⚠️  Could not query demo tables (schema not applied yet?) -- skipping seed step." >&2
    return
  fi

  if [ "$status_line" = "empty" ]; then
    if [ ! -f "${CIR_DIR}/run-demo.sh" ]; then
      echo "⚠️  '${CIR_DIR}/run-demo.sh' not found -- cannot start seed-demo workflow." >&2
      return
    fi
    echo "🌱 Demo tables are empty -- starting seed-demo workflow via ${CIR_DIR}/run-demo.sh..."
    (cd "$CIR_DIR" && bash run-demo.sh)
    print_database_status
  else
    echo "🟢 Demo tables already contain data -- skipping seed-demo workflow."
    print_database_status
  fi
}

if [ "$SKIP_SEED" = "true" ]; then
  echo "⏭️  --no-seed set -- skipping CIR demo data seed check."
else
  seed_demo_if_empty
fi

print_final_service_table() {
  local postgres_status redis_status keycloak_status minio_status
  postgres_status="$(docker inspect -f '{{.State.Health.Status}}' "$POSTGRES_CONTAINER" 2>/dev/null || echo "unknown")"
  redis_status="$(docker inspect -f '{{.State.Health.Status}}' "$REDIS_CONTAINER" 2>/dev/null || echo "unknown")"
  keycloak_status="$(docker inspect -f '{{.State.Health.Status}}' "$KEYCLOAK_CONTAINER" 2>/dev/null || echo "unknown")"
  minio_status="$(docker inspect -f '{{.State.Health.Status}}' "$MINIO_CONTAINER" 2>/dev/null || echo "unknown")"

  echo ""
  echo "✅ Core services status"
  printf '%-12s | %-10s | %-25s\n' "Service" "Status" "Host:Port"
  printf '%-12s-+-%-10s-+-%-25s\n' "------------" "----------" "-------------------------"
  printf '%-12s | %-10s | %-25s\n' "postgres" "$postgres_status" "localhost:${POSTGRES_HOST_PORT:-5432}"
  printf '%-12s | %-10s | %-25s\n' "redis" "$redis_status" "localhost:${REDIS_HOST_PORT:-6379}"
  printf '%-12s | %-10s | %-25s\n' "keycloak" "$keycloak_status" "localhost:${KEYCLOAK_HOST_PORT:-8080}"
  printf '%-12s | %-10s | %-25s\n' "minio" "$minio_status" "localhost:${MINIO_API_HOST_PORT:-9000} (console ${MINIO_CONSOLE_HOST_PORT:-9001})"
}

print_final_service_table