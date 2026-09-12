
#!/bin/bash
# =============================================================================
# Customer 360 Platform - local DEV bootstrap
#
# Starts the development stack in dev-docker-compose.yml (postgres + redis +
# keycloak + minio + tracking-api) and the local docs-vector-search service so
# customer360-api and
# backend-system/identity_resolution (CIR) can be run directly on the host
# against dockerized Postgres/Redis -- see
# customer360-api/start.sh and backend-system/identity_resolution/run-demo.sh, and
# "non-Docker local dev workflow" in DOCKER-COMPOSE-GUIDE.md section 10.
#
# What it does, in order:
#   1. Ensures '.env' exists (created from '.env.example' if missing) and
#      contains every key currently in '.env.example'.
#   2. Starts (or resets) postgres/redis/keycloak/minio via
#      `docker compose -f dev-docker-compose.yml`.
#   3. Builds the docs-vector-search index against the local PostgreSQL service
#      and starts the AI service on DOCS_SEARCH_HOST_PORT.
#   4. Waits for postgres/redis/keycloak/minio/tracking-api/docs-vector-search containers to
#      report healthy, then waits for the one-shot `minio-init` bucket-bootstrap
#      job to complete.
#   5. Checks whether the Keycloak 'leocdp' realm exists yet; there is no
#      automated realm/client seed script in this repo, so it prints manual
#      setup instructions (DOCKER-COMPOSE-GUIDE.md section 9) when missing.
#   6. Checks whether core demo tables are empty; if empty, runs the
#      seed-demo workflow via backend-system/identity_resolution/run-demo.sh.
#      If not empty, prints current DB row-count status for key tables.
#
# Usage:
#   ./dev-c360.sh                   Start/create services, sync .env, run
#                                    seed-demo only when DB is empty; otherwise
#                                    print DB status counts.
#   ./dev-c360.sh no-seed           Same, but skip the CIR demo data seed step.
#   ./dev-c360.sh upgrade           Local DEV upgrade: refresh images/containers
#                                    with current repo code and restart core
#                                    host services (non-destructive).
#   ./dev-c360.sh restart           Restart docs-vector-search,
#                                    customer360-api, backend-system, and frontend-admin.
#   ./dev-c360.sh reset             DESTRUCTIVE: `docker compose down -v`
#                                    (drops the postgres/redis/minio volumes
#                                    -- this also wipes Keycloak's
#                                    db_keycloak and the MinIO dev bucket)
#                                    then starts fresh and reseeds.
#   ./dev-c360.sh reset -y          Same as 'reset' but skips the confirmation
#                                    prompt (CI / automation).
#   ./dev-c360.sh stop-all          DESTRUCTIVE: stop and remove all development
#                                    containers, volumes, and the shared network.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_FILE=""
ENV_FILE=".env"
ENV_EXAMPLE_FILE=".env.example"
CIR_DIR="backend-system/identity_resolution"
BACKEND_SYSTEM_DIR="backend-system"
CUSTOMER360_API_DIR="customer360-api"
FRONTEND_ADMIN_DIR="frontend-admin"
DOCS_SEARCH_DIR="tools/docs-vector-search"
DOCS_SEARCH_COMPOSE_FILE="$DOCS_SEARCH_DIR/docker-compose.yml"
DOCS_SEARCH_ENV_FILE="$DOCS_SEARCH_DIR/.env"
DOCS_SEARCH_CONTAINER="docs-vector-search"
POSTGRES_CONTAINER="customer360-postgres"
REDIS_CONTAINER="customer360-redis"
KEYCLOAK_CONTAINER="customer360-keycloak"
MINIO_CONTAINER="customer360-minio"
MINIO_INIT_CONTAINER="customer360-minio-init"
TRACKING_CONTAINER="customer360-tracking-api"

# --- Parse args (order-independent) ---
ACTION="up"
SKIP_CONFIRM="false"
SKIP_SEED="false"
for arg in "$@"; do
  case "$arg" in
    upgrade) ACTION="upgrade" ;;
    restart) ACTION="restart" ;;
    reset) ACTION="reset" ;;
    stop-all) ACTION="stop-all" ;;
    -y|--yes) SKIP_CONFIRM="true" ;;
    no-seed) SKIP_SEED="true" ;;
    -h|--help)
      sed -n '2,46p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "❌ Unknown argument: $arg (use -h for usage)" >&2
      exit 1
      ;;
  esac
done

if [ "$ACTION" = "stop-all" ]; then
  bash "$SCRIPT_DIR/dev-stop-and-delete-all.sh"
  exit 0
fi

# 'upgrade' refreshes all dev containers/images and restarts host services
# without touching persistent volumes.
if [ "$ACTION" = "upgrade" ]; then
  SKIP_SEED="true"
fi

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
          echo "# --- Added by dev-c360.sh on $(date +%Y-%m-%d) from ${ENV_EXAMPLE_FILE} ---"
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

# Choose compose file based on SSO_LOGIN.
# - SSO_LOGIN=true  => dev-docker-compose.yml
# - SSO_LOGIN=false => dev-no-sso-docker-compose.yml
case "${SSO_LOGIN:-true}" in
  true)
    COMPOSE_FILE="dev-docker-compose.yml"
    ;;
  false)
    COMPOSE_FILE="dev-no-sso-docker-compose.yml"
    ;;
  *)
    echo "❌ Error: SSO_LOGIN must be 'true' or 'false' in '${ENV_FILE}' (current: '${SSO_LOGIN:-}')." >&2
    exit 1
    ;;
esac
DC_CMD=("${DC[@]}" -f "$COMPOSE_FILE")
echo "🔧 SSO_LOGIN=${SSO_LOGIN:-true} -> using compose file '${COMPOSE_FILE}'."

DOCS_SEARCH_HOST_PORT="${DOCS_SEARCH_HOST_PORT:-8001}"
DOCS_SEARCH_URL="${DOCS_SEARCH_URL:-http://127.0.0.1:${DOCS_SEARCH_HOST_PORT}}"
DOCS_SEARCH_TIMEOUT="${DOCS_PROXY_TIMEOUT_SECONDS:-${DOCS_SEARCH_TIMEOUT:-120}}"

docker_gpu_available() {
  command -v nvidia-smi >/dev/null 2>&1 || return 1
  nvidia-smi -L >/dev/null 2>&1 || return 1

  # Compose's `gpus: all` requires either Docker's NVIDIA runtime or a
  # registered NVIDIA CDI specification. Do not trust nvidia-smi alone.
  local runtimes
  runtimes="$(docker info --format '{{json .Runtimes}}' 2>/dev/null || true)"
  if [[ "$runtimes" == *'"nvidia"'* ]]; then
    return 0
  fi
  for cdi_spec in /etc/cdi/nvidia.yaml /var/run/cdi/nvidia.yaml; do
    if [ -f "$cdi_spec" ] && grep -q "nvidia.com/gpu" "$cdi_spec"; then
      return 0
    fi
  done
  return 1
}

DOCS_GPU_REQUEST_VALUE="${DOCS_GPU_REQUEST:-}"
if [ -z "$DOCS_GPU_REQUEST_VALUE" ]; then
  if docker_gpu_available; then
    DOCS_GPU_REQUEST="all"
    DOCS_SEARCH_SERVICE="docs-vector-search-gpu"
    DOCS_SEARCH_CONTAINER="docs-vector-search-gpu"
    echo "🎮 NVIDIA GPU and Docker GPU support detected -- enabling docs-service GPU access."
  else
    DOCS_GPU_REQUEST="0"
    DOCS_SEARCH_SERVICE="docs-vector-search"
    echo "🖥️  No usable NVIDIA GPU/Docker GPU support detected -- using CPU docs-service mode."
  fi
elif [ "$DOCS_GPU_REQUEST_VALUE" = "all" ]; then
  if docker_gpu_available; then
    DOCS_SEARCH_SERVICE="docs-vector-search-gpu"
    DOCS_SEARCH_CONTAINER="docs-vector-search-gpu"
    echo "🎮 NVIDIA GPU and Docker GPU support detected -- enabling docs-service GPU access."
  else
    DOCS_GPU_REQUEST="0"
    DOCS_SEARCH_SERVICE="docs-vector-search"
    echo "⚠️  GPU was requested, but NVIDIA hardware/Docker GPU support is unavailable -- using CPU docs-service mode."
  fi
else
  DOCS_GPU_REQUEST="0"
  DOCS_SEARCH_SERVICE="docs-vector-search"
  echo "🖥️  GPU disabled by DOCS_GPU_REQUEST=${DOCS_GPU_REQUEST_VALUE} -- using CPU docs-service mode."
fi
export DOCS_SEARCH_URL DOCS_SEARCH_TIMEOUT DOCS_GPU_REQUEST DOCS_SEARCH_SERVICE
DOCS_DC_CMD=("${DC[@]}" --profile cpu --profile gpu --env-file "$DOCS_SEARCH_ENV_FILE" -f "$DOCS_SEARCH_COMPOSE_FILE")

ensure_docs_env_file() {
  if [ -f "$DOCS_SEARCH_ENV_FILE" ]; then
    return
  fi

  echo "📄 '${DOCS_SEARCH_ENV_FILE}' not found -- creating local docs-service configuration..."
  umask 077
  cat > "$DOCS_SEARCH_ENV_FILE" <<EOF
# Generated by dev-c360.sh. Edit this file to use a different vector database.
PG_HOST=postgres
PG_PORT=5432
PG_DATABASE=${DB_NAME:-customer360}
PG_USER=${DB_USER:-postgres}
PG_PASSWORD=${DB_PASSWORD:-}
PG_SCHEMA=rag
API_PORT=${DOCS_SEARCH_HOST_PORT}
# --- Providers and models ---
# DEFAULT RUN CONFIGURATION: OpenAI is selected for embeddings and generation.
# Set DOCS_OPENAI_API_KEY before running enrich, /search, or /ask.
DOCS_RERANK_ENABLED=${DOCS_RERANK_ENABLED:-true}
DOCS_RERANK_MODEL=${DOCS_RERANK_MODEL:-BAAI/bge-reranker-base}
DOCS_LLM_MAX_OUTPUT_TOKENS=${DOCS_LLM_MAX_OUTPUT_TOKENS:-256}
DOCS_EMBEDDING_PROVIDER=${DOCS_EMBEDDING_PROVIDER:-openai}
DOCS_LLM_PROVIDER=${DOCS_LLM_PROVIDER:-openai}
# REQUIRED DEFAULT CREDENTIAL: set this secret to use the default OpenAI providers.
DOCS_OPENAI_API_KEY=${DOCS_OPENAI_API_KEY:-}
# OpenAI embedding and LLM settings.
DOCS_OPENAI_API_BASE_URL=${DOCS_OPENAI_API_BASE_URL:-https://api.openai.com/v1}
DOCS_OPENAI_REQUEST_TIMEOUT_SECONDS=${DOCS_OPENAI_REQUEST_TIMEOUT_SECONDS:-120}
DOCS_OPENAI_EMBEDDING_MODEL=${DOCS_OPENAI_EMBEDDING_MODEL:-text-embedding-3-small}
DOCS_OPENAI_EMBEDDING_DIMENSIONS=${DOCS_OPENAI_EMBEDDING_DIMENSIONS:-384}
DOCS_OPENAI_LLM_MODEL=${DOCS_OPENAI_LLM_MODEL:-gpt-5.6-luna}
# Optional Gemini embedding and LLM settings.
DOCS_GEMINI_API_KEY=${DOCS_GEMINI_API_KEY:-}
DOCS_GEMINI_API_BASE_URL=${DOCS_GEMINI_API_BASE_URL:-https://generativelanguage.googleapis.com/v1beta}
DOCS_GEMINI_REQUEST_TIMEOUT_SECONDS=${DOCS_GEMINI_REQUEST_TIMEOUT_SECONDS:-120}
DOCS_GEMINI_EMBEDDING_MODEL=${DOCS_GEMINI_EMBEDDING_MODEL:-gemini-embedding-001}
DOCS_GEMINI_EMBEDDING_DIMENSIONS=${DOCS_GEMINI_EMBEDDING_DIMENSIONS:-384}
DOCS_GEMINI_LLM_MODEL=${DOCS_GEMINI_LLM_MODEL:-gemini-2.5-flash}
# Optional local fastembed embedding and Qwen LLM settings.
DOCS_LOCAL_EMBEDDING_MODEL=${DOCS_LOCAL_EMBEDDING_MODEL:-sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2}
DOCS_LOCAL_EMBEDDING_DIMENSIONS=${DOCS_LOCAL_EMBEDDING_DIMENSIONS:-384}
DOCS_LOCAL_LLM_MODEL_PATH=${DOCS_LOCAL_LLM_MODEL_PATH:-/app/models/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf}
DOCS_LOCAL_LLM_CONTEXT_TOKENS=${DOCS_LOCAL_LLM_CONTEXT_TOKENS:-2048}
DOCS_LOCAL_LLM_THREADS=${DOCS_LOCAL_LLM_THREADS:-2}
DOCS_LOCAL_LLM_BATCH_SIZE=${DOCS_LOCAL_LLM_BATCH_SIZE:-512}
DOCS_LOCAL_LLM_GPU_LAYERS=${DOCS_LOCAL_LLM_GPU_LAYERS:--1}
# --- Retrieval / chunking ---
RETRIEVE_TOP_N=${RETRIEVE_TOP_N:-50}
RERANK_TOP_K=${RERANK_TOP_K:-5}
CHUNK_TOKENS=${CHUNK_TOKENS:-400}
CHUNK_OVERLAP=${CHUNK_OVERLAP:-50}
CONTEXT_CHAR_BUDGET=${CONTEXT_CHAR_BUDGET:-6000}
# --- HTTP / browser access ---
CORS_ORIGINS=${CORS_ORIGINS:-https://leo-cdp.github.io}
ASK_RATE_MAX=${ASK_RATE_MAX:-10}
ASK_RATE_WINDOW_SEC=${ASK_RATE_WINDOW_SEC:-60}
TRUSTED_PROXY_HOPS=${TRUSTED_PROXY_HOPS:-1}
TOP_N_MAX=${TOP_N_MAX:-50}
TOP_K_MAX=${TOP_K_MAX:-20}
QUESTION_MAX_LEN=${QUESTION_MAX_LEN:-2000}
DOCS_REDIS_HOST=${DOCS_REDIS_HOST:-docs-rate-limit-redis}
DOCS_REDIS_PORT=${DOCS_REDIS_PORT:-6580}
DOCS_REDIS_DB=${DOCS_REDIS_DB:-0}
DOCS_REDIS_PASSWORD=${DOCS_REDIS_PASSWORD:-}
DOCS_REDIS_CONNECT_TIMEOUT_SECONDS=${DOCS_REDIS_CONNECT_TIMEOUT_SECONDS:-1}
DOCS_REDIS_SOCKET_TIMEOUT_SECONDS=${DOCS_REDIS_SOCKET_TIMEOUT_SECONDS:-1}
INTERNAL_API_SECRET=${DOCS_INTERNAL_AUTH_SECRET:-${DOCS_INTERNAL_SECRET:-}}
EOF
}

load_docs_provider_env() {
  local line key value
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != DOCS_*\=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    export "$key=$value"
  done < "$DOCS_SEARCH_ENV_FILE"

  # docs-search owns its local limiter Redis; do not inherit the authenticated
  # customer360 cache settings from the root .env.
  export DOCS_REDIS_HOST=docs-rate-limit-redis
  export DOCS_REDIS_PASSWORD=
}

validate_docs_provider_credentials() {
  local embedding_provider llm_provider
  embedding_provider="$(sed -n 's/^DOCS_EMBEDDING_PROVIDER=//p' "$DOCS_SEARCH_ENV_FILE" | head -1)"
  llm_provider="$(sed -n 's/^DOCS_LLM_PROVIDER=//p' "$DOCS_SEARCH_ENV_FILE" | head -1)"
  if [[ "$embedding_provider" == "openai" || "$llm_provider" == "openai" ]] && \
     ! grep -q '^DOCS_OPENAI_API_KEY=.' "$DOCS_SEARCH_ENV_FILE"; then
    echo "❌ DOCS_OPENAI_API_KEY is required because DOCS_EMBEDDING_PROVIDER or DOCS_LLM_PROVIDER is openai." >&2
    echo "   Set it in '$DOCS_SEARCH_ENV_FILE' or select local/gemini providers." >&2
    exit 1
  fi
}

restart_host_services() {
  echo "🔁 Restarting host services..."
  echo "   - backend-system: ./${BACKEND_SYSTEM_DIR}/restart.sh"
  (cd "$BACKEND_SYSTEM_DIR" && bash restart.sh)

  echo "   - customer360-api: ./${CUSTOMER360_API_DIR}/restart.sh"
  (cd "$CUSTOMER360_API_DIR" && bash restart.sh)

  echo "   - frontend-admin: ./${FRONTEND_ADMIN_DIR}/restart.sh"
  (cd "$FRONTEND_ADMIN_DIR" && bash restart.sh)
}

start_docs_service() {
  ensure_docs_env_file
  load_docs_provider_env
  validate_docs_provider_credentials
  echo "🤖 Building local docs-vector-search image..."
  "${DOCS_DC_CMD[@]}" build

  echo "📚 Refreshing docs-vector-search index..."
  "${DOCS_DC_CMD[@]}" run --rm "$DOCS_SEARCH_SERVICE" python -m src.enrich

  local force_recreate="${1:-false}"
  local up_args=(-d --build)
  if [ "$force_recreate" = "true" ]; then
    up_args+=(--force-recreate)
  fi
  echo "🤖 Starting docs-vector-search on http://127.0.0.1:${DOCS_SEARCH_HOST_PORT}..."
  "${DOCS_DC_CMD[@]}" up "${up_args[@]}" "$DOCS_SEARCH_SERVICE"
}

restart_docs_service() {
  ensure_docs_env_file
  load_docs_provider_env
  validate_docs_provider_credentials
  echo "🔁 Restarting docs-vector-search..."
  "${DOCS_DC_CMD[@]}" up -d --build --force-recreate "$DOCS_SEARCH_SERVICE"
}

reset_docs_service() {
  ensure_docs_env_file
  load_docs_provider_env
  echo "🗑️  Removing docs-vector-search container..."
  "${DOCS_DC_CMD[@]}" down -v --remove-orphans
}

upgrade_docs_service() {
  ensure_docs_env_file
  load_docs_provider_env
  validate_docs_provider_credentials
  echo "⬆️  Refreshing docs-vector-search image..."
  "${DOCS_DC_CMD[@]}" pull --ignore-pull-failures || true
  start_docs_service true
}

wait_for_docs_healthy() {
  local max_attempts="${1:-90}"
  local attempt=1
  echo "⏳ Waiting for '${DOCS_SEARCH_CONTAINER}' to become healthy (max ${max_attempts} attempts)..."
  until [ "$(docker inspect -f '{{.State.Health.Status}}' "$DOCS_SEARCH_CONTAINER" 2>/dev/null)" = "healthy" ]; do
    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "❌ Error: '${DOCS_SEARCH_CONTAINER}' did not become healthy after ${max_attempts} attempts." >&2
      docker logs --tail=80 "$DOCS_SEARCH_CONTAINER" 2>/dev/null || true
      exit 1
    fi
    sleep 2
    attempt=$((attempt + 1))
  done
  echo "🟢 '${DOCS_SEARCH_CONTAINER}' is healthy."
}

if [ "$ACTION" = "restart" ]; then
  restart_docs_service
  wait_for_docs_healthy
  restart_host_services
  exit 0
fi

# DB_PORT/REDIS_PORT are what host-run apps (customer360-api/start.sh,
# backend-system/identity_resolution/run-demo.sh) connect through; *_HOST_PORT is
# what docker-compose publishes. They must match when running against the
# dockerized services from the host.
if [ "${DB_PORT:-5432}" != "${POSTGRES_HOST_PORT:-5432}" ]; then
  echo "⚠️  DB_PORT (${DB_PORT:-5432}) != POSTGRES_HOST_PORT (${POSTGRES_HOST_PORT:-5432}) in '${ENV_FILE}' -- host-run apps connecting via DB_PORT may not reach the published Postgres port."
fi
if [ "${REDIS_PORT:-6580}" != "${REDIS_HOST_PORT:-6580}" ]; then
  echo "⚠️  REDIS_PORT (${REDIS_PORT:-6580}) != REDIS_HOST_PORT (${REDIS_HOST_PORT:-6580}) in '${ENV_FILE}' -- host-run apps connecting via REDIS_PORT may not reach the published Redis port."
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
  reset_docs_service
  echo "🗑️  Tearing down existing containers + volumes..."
  "${DC_CMD[@]}" down -v
fi

if [ "$ACTION" = "upgrade" ]; then
  echo "⬆️  Upgrading local dev services with latest repo state (${COMPOSE_FILE})..."
  echo "   - Pulling latest base images (non-fatal when some images are local-only)..."
  "${DC_CMD[@]}" pull --ignore-pull-failures || true
  echo "   - Rebuilding and force-recreating containers without deleting volumes..."
  "${DC_CMD[@]}" up -d --build --force-recreate
else
  echo "🚀 Starting postgres + redis + keycloak + minio (${COMPOSE_FILE})..."
  "${DC_CMD[@]}" up -d --build
fi

# =============================================================================
# 3) Wait for the healthchecked services, then for the one-shot minio-init
#    bucket-bootstrap job to finish.
# =============================================================================
wait_for_healthy() {
  local container="$1"
  local max_attempts="${2:-30}"
  local attempt=1
  echo "⏳ Waiting for '${container}' to become healthy (max ${max_attempts} attempts)..."

  # First, wait for container to exist
  until docker inspect "$container" >/dev/null 2>&1; do
    if [ "$attempt" -ge 10 ]; then
      echo "❌ Error: Container '${container}' was not created after 10 attempts." >&2
      "${DC_CMD[@]}" logs --tail=50 "$container" 2>/dev/null || echo "(logs unavailable)"
      exit 1
    fi
    sleep 1
    attempt=$((attempt + 1))
  done

  # Then wait for health status
  attempt=1
  until [ "$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null)" = "healthy" ]; do
    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "❌ Error: '${container}' did not become healthy after ${max_attempts} attempts." >&2
      echo "Container status: $(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo 'unknown')"
      "${DC_CMD[@]}" logs --tail=50 "$container" 2>/dev/null || true
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
wait_for_healthy "$MINIO_CONTAINER"
wait_for_healthy "$TRACKING_CONTAINER"
wait_for_completed "$MINIO_INIT_CONTAINER"

if [[ "${SSO_LOGIN:-true}" == "true" ]]; then
  # Keycloak is slow on first boot (~40s to start + 60s Docker start_period before
  # health probes count), so give it a longer leash than the other services.
  wait_for_healthy "$KEYCLOAK_CONTAINER" 90
fi

if [ "$ACTION" = "upgrade" ]; then
  upgrade_docs_service
else
  start_docs_service
fi
wait_for_docs_healthy

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
  if [ "$ACTION" = "upgrade" ]; then
    echo "⏭️  Upgrade mode -- skipping CIR demo data seed step."
  else
    echo "⏭️  --no-seed set -- skipping CIR demo data seed check."
  fi
else
  seed_demo_if_empty
fi

get_host_service_status() {
  local pid_file="$1"
  local pid
  if [ -f "$pid_file" ]; then
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      echo "running"
      return
    fi
  fi
  echo "stopped"
}

restart_host_services

print_final_service_table() {
  local postgres_status redis_status minio_status tracking_status docs_status
  local backend_status api_status frontend_status
  postgres_status="$(docker inspect -f '{{.State.Health.Status}}' "$POSTGRES_CONTAINER" 2>/dev/null || echo "unknown")"
  redis_status="$(docker inspect -f '{{.State.Health.Status}}' "$REDIS_CONTAINER" 2>/dev/null || echo "unknown")"
  minio_status="$(docker inspect -f '{{.State.Health.Status}}' "$MINIO_CONTAINER" 2>/dev/null || echo "unknown")"
  tracking_status="$(docker inspect -f '{{.State.Health.Status}}' "$TRACKING_CONTAINER" 2>/dev/null || echo "unknown")"
  docs_status="$(docker inspect -f '{{.State.Health.Status}}' "$DOCS_SEARCH_CONTAINER" 2>/dev/null || echo "unknown")"
  backend_status="$(get_host_service_status "$SCRIPT_DIR/$BACKEND_SYSTEM_DIR/.dagster.pid")"
  api_status="$(get_host_service_status "$SCRIPT_DIR/$CUSTOMER360_API_DIR/.uvicorn.pid")"
  frontend_status="$(get_host_service_status "$SCRIPT_DIR/$FRONTEND_ADMIN_DIR/.uvicorn.pid")"

  echo ""
  echo "✅ Services status"
  printf '%-12s | %-10s | %-25s\n' "Service" "Status" "Host:Port"
  printf '%-12s-+-%-10s-+-%-25s\n' "------------" "----------" "-------------------------"
  printf '%-12s | %-10s | %-25s\n' "postgres" "$postgres_status" "localhost:${POSTGRES_HOST_PORT:-5432}"
  printf '%-12s | %-10s | %-25s\n' "redis" "$redis_status" "localhost:${REDIS_HOST_PORT:-6580}"
  printf '%-12s | %-10s | %-25s\n' "minio" "$minio_status" "localhost:${MINIO_API_HOST_PORT:-9000} (console ${MINIO_CONSOLE_HOST_PORT:-9001})"
  printf '%-12s | %-10s | %-25s\n' "tracking-api" "$tracking_status" "localhost:${C360_TRACKING_API_PORT:-8010}"
  printf '%-12s | %-10s | %-25s\n' "docs-ai" "$docs_status" "localhost:${DOCS_SEARCH_HOST_PORT} (/health)"
  printf '%-12s | %-10s | %-25s\n' "backend" "$backend_status" "localhost:${DAGSTER_UI_PORT:-3000}"
  printf '%-12s | %-10s | %-25s\n' "api" "$api_status" "localhost:${C360_API_PORT:-8008}"
  printf '%-12s | %-10s | %-25s\n' "frontend" "$frontend_status" "localhost:${FRONTEND_HOST_PORT:-8890}"

  if [[ "${SSO_LOGIN:-true}" == "true" ]]; then
    # Keycloak is optional when SSO_LOGIN=false, so only print its status when SSO_LOGIN=true.
    local keycloak_status
    keycloak_status="$(docker inspect -f '{{.State.Health.Status}}' "$KEYCLOAK_CONTAINER" 2>/dev/null || echo "unknown")"
    printf '%-12s | %-10s | %-25s\n' "keycloak" "$keycloak_status" "localhost:${KEYCLOAK_HOST_PORT:-8080}"
  fi
}

print_final_service_table