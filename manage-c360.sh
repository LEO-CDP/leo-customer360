#!/bin/bash
# =============================================================================
# Customer 360 Platform - PRODUCTION service manager (Ubuntu server)
#
# Starts/stops/restarts the full production Docker Compose stack
# (docker-compose.yml: postgres + redis + keycloak + cir + api -- the `dev`
# profile / cir-demo-seed job is intentionally never included here).
#
# On first run (no '.env' present), interactively asks for the handful of
# values production actually needs (DB/Redis/Keycloak passwords, public
# hostname, ...) using '.env.example' as the template, then writes '.env'.
# If stdin isn't a terminal (e.g. invoked from cron/CI), missing secrets are
# auto-generated instead of prompted, and printed once so you can save them.
#
# Usage:
#   ./manage-c360.sh start           Ensure .env, then build+start all services.
#   ./manage-c360.sh stop [service]  Stop container(s); keep them + volumes/network.
#   ./manage-c360.sh restart [service]  Restart all (or one) service.
#   ./manage-c360.sh status          Show docker compose ps + per-container health.
#   ./manage-c360.sh logs [service]  Follow logs (all services, or one).
#   ./manage-c360.sh build           Rebuild images without starting anything.
#   ./manage-c360.sh down            Stop + remove containers/network (volumes kept).
#   ./manage-c360.sh seed-demo       Seed full POC/UAT demo data (CIR + content).
#   ./manage-c360.sh help            Show this usage.
#
# Flags:
#   --force   With 'start': proceed even if required secrets still look like
#             the '.env.example' placeholder values (NOT recommended).
#
# See DOCKER-COMPOSE-GUIDE.md for the full operations reference.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"
ENV_EXAMPLE_FILE=".env.example"

POSTGRES_CONTAINER="customer360-postgres"
REDIS_CONTAINER="customer360-redis"
KEYCLOAK_CONTAINER="customer360-keycloak"
CIR_CONTAINER="customer360-cir"
API_CONTAINER="customer360-api"
ALL_CONTAINERS=("$POSTGRES_CONTAINER" "$REDIS_CONTAINER" "$KEYCLOAK_CONTAINER" "$CIR_CONTAINER" "$API_CONTAINER")

# Required secrets that MUST be changed from the .env.example placeholder
# before we'll let 'start' bring up production (see docker-compose.yml's
# ${VAR:?...} guards for DB_PASSWORD/REDIS_PASSWORD/KEYCLOAK_ADMIN_PASSWORD).
declare -A REQUIRED_PLACEHOLDERS=(
  [DB_PASSWORD]="change_me_postgres_password"
  [REDIS_PASSWORD]="change_me_redis_password"
  [KEYCLOAK_ADMIN_PASSWORD]="change_me_keycloak_admin_password"
)

# --- Parse args (order-independent) ---
COMMAND=""
SERVICE_ARG=""
FORCE="false"
for arg in "$@"; do
  case "$arg" in
    start|stop|restart|status|ps|logs|build|down|help|-h|--help)
      [ "$arg" = "-h" ] || [ "$arg" = "--help" ] && arg="help"
      COMMAND="$arg"
      ;;
    seed-demo)
      COMMAND="$arg"
      ;;
    --force) FORCE="true" ;;
    *)
      if [ -z "$SERVICE_ARG" ]; then
        SERVICE_ARG="$arg"
      else
        echo "❌ Unknown argument: $arg" >&2
        exit 1
      fi
      ;;
  esac
done

usage() {
  sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
}

if [ -z "$COMMAND" ] || [ "$COMMAND" = "help" ]; then
  usage
  exit 0
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
DC_CMD=("${DC[@]}" -f "$COMPOSE_FILE")

# =============================================================================
# .env bootstrap
# =============================================================================
random_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 24 | tr -d '\n=/+' | cut -c1-32
  else
    head -c 32 /dev/urandom | base64 | tr -d '\n=/+' | cut -c1-32
  fi
}

set_env_value() {
  local key="$1" value="$2" escaped
  escaped=$(printf '%s' "$value" | sed -e 's/[&#\]/\\&/g')
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s#^${key}=.*#${key}=${escaped}#" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

get_env_value() {
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2-
}

# Prompts for a plain (non-secret) value with a shown default.
prompt_value() {
  local prompt_text="$1" default_value="$2" answer
  read -r -p "$prompt_text [${default_value}]: " answer || answer=""
  echo "${answer:-$default_value}"
}

# Prompts for a secret (hidden input). Empty input -> auto-generate.
prompt_secret_or_generate() {
  local prompt_text="$1" answer
  read -r -s -p "$prompt_text (leave blank to auto-generate): " answer || answer=""
  echo "" >&2
  if [ -z "$answer" ]; then
    answer="$(random_secret)"
    echo "   ↳ generated: ${answer}" >&2
  fi
  echo "$answer"
}

# Interactive first-time .env setup, using .env.example as the template.
bootstrap_env_interactive() {
  echo "📄 '${ENV_FILE}' not found -- let's create it from '${ENV_EXAMPLE_FILE}'." >&2
  cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"

  echo "" >&2
  echo "── PostgreSQL ──────────────────────────────────────────────" >&2
  set_env_value DB_USER "$(prompt_value 'DB user' 'postgres')"
  set_env_value DB_NAME "$(prompt_value 'DB name' 'customer360')"
  set_env_value DB_PASSWORD "$(prompt_secret_or_generate 'DB password')"

  echo "" >&2
  echo "── Redis ───────────────────────────────────────────────────" >&2
  set_env_value REDIS_PASSWORD "$(prompt_secret_or_generate 'Redis password')"

  echo "" >&2
  echo "── Keycloak (SSO) ──────────────────────────────────────────" >&2
  set_env_value KEYCLOAK_ADMIN "$(prompt_value 'Keycloak admin username' 'admin')"
  set_env_value KEYCLOAK_ADMIN_PASSWORD "$(prompt_secret_or_generate 'Keycloak admin password')"
  local hostname
  hostname="$(prompt_value 'Public hostname/domain for this server (used by Keycloak + frontend)' 'localhost')"
  set_env_value KEYCLOAK_HOSTNAME "$hostname"
  set_env_value FRONTEND_API_HOSTNAME "http://${hostname}:$(get_env_value API_HOST_PORT || echo 8000)"
  local real_prod
  real_prod="$(prompt_value "Run Keycloak in production mode ('start', requires TLS/reverse proxy) instead of 'start-dev'? [y/N]" 'N')"
  case "$real_prod" in
    y|Y|yes|YES) set_env_value KEYCLOAK_COMMAND "start" ;;
    *) set_env_value KEYCLOAK_COMMAND "start-dev" ;;
  esac
  echo "   ℹ️  KEYCLOAK_CLIENT_SECRET is left as a placeholder -- create the" >&2
  echo "      'leocdp' realm/client first (DOCKER-COMPOSE-GUIDE.md §9), then" >&2
  echo "      set it in '${ENV_FILE}' and run: ./manage-c360.sh restart api" >&2

  echo "" >&2
  echo "── Network exposure ────────────────────────────────────────" >&2
  local expose
  expose="$(prompt_value 'Expose Postgres/Redis/Keycloak/API ports on all interfaces (0.0.0.0) instead of localhost-only? [y/N]' 'N')"
  if [[ "$expose" =~ ^[yY] ]]; then
    for bind_var in POSTGRES_HOST_BIND REDIS_HOST_BIND API_HOST_BIND KEYCLOAK_HOST_BIND FRONTEND_HOST_BIND; do
      set_env_value "$bind_var" "0.0.0.0"
    done
    echo "   ⚠️  Ports bound to 0.0.0.0 -- make sure this host's firewall (ufw/security group) is locked down, or put a reverse proxy in front." >&2
  fi

  echo "" >&2
  echo "✅ '${ENV_FILE}' created. Review it before going further:" >&2
  echo "   ${SCRIPT_DIR}/${ENV_FILE}" >&2
}

# Non-interactive (no TTY) first-time .env setup: auto-generate every
# required secret so automated deploys (cron/CI/provisioning scripts) don't
# hang on a read prompt, then print them once.
bootstrap_env_noninteractive() {
  echo "📄 '${ENV_FILE}' not found and no interactive terminal detected -- creating it from '${ENV_EXAMPLE_FILE}' with auto-generated secrets." >&2
  cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"

  local db_pw redis_pw kc_pw
  db_pw="$(random_secret)"
  redis_pw="$(random_secret)"
  kc_pw="$(random_secret)"
  set_env_value DB_PASSWORD "$db_pw"
  set_env_value REDIS_PASSWORD "$redis_pw"
  set_env_value KEYCLOAK_ADMIN_PASSWORD "$kc_pw"

  cat >&2 <<EOF

⚠️  AUTO-GENERATED CREDENTIALS -- shown only this once, save them now:
    DB_PASSWORD             = ${db_pw}
    REDIS_PASSWORD          = ${redis_pw}
    KEYCLOAK_ADMIN_PASSWORD = ${kc_pw}

   Edit '${ENV_FILE}' afterwards to set KEYCLOAK_HOSTNAME, KEYCLOAK_CLIENT_SECRET
   (after creating the Keycloak realm/client -- DOCKER-COMPOSE-GUIDE.md §9),
   and any *_HOST_BIND values if this needs to be reachable off localhost.
EOF
}

ensure_env_file() {
  if [ ! -f "$ENV_FILE" ]; then
    if [ ! -f "$ENV_EXAMPLE_FILE" ]; then
      echo "❌ Error: neither '${ENV_FILE}' nor '${ENV_EXAMPLE_FILE}' found in ${SCRIPT_DIR}." >&2
      exit 1
    fi
    if [ -t 0 ]; then
      bootstrap_env_interactive
    else
      bootstrap_env_noninteractive
    fi
  fi
}

# Adds any key present in .env.example but missing from .env (without
# touching values the user already customized).
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
          echo "# --- Added by manage-c360.sh on $(date +%Y-%m-%d) from ${ENV_EXAMPLE_FILE} ---"
        } >> "$ENV_FILE"
      fi
      echo "$line" >> "$ENV_FILE"
      echo "➕ Added missing key '${key}' to '${ENV_FILE}' (review its value)." >&2
      added=$((added + 1))
    fi
  done < "$ENV_EXAMPLE_FILE"
}

# Refuses to start production with obviously-unset required secrets.
validate_required_secrets() {
  if [ "$FORCE" = "true" ]; then
    return 0
  fi
  local bad=0
  local key placeholder current
  for key in "${!REQUIRED_PLACEHOLDERS[@]}"; do
    placeholder="${REQUIRED_PLACEHOLDERS[$key]}"
    current="$(get_env_value "$key")"
    if [ -z "$current" ] || [ "$current" = "$placeholder" ]; then
      echo "❌ '${key}' in '${ENV_FILE}' is unset or still the placeholder value." >&2
      bad=1
    fi
  done
  if [ "$bad" -ne 0 ]; then
    echo "   Edit '${ENV_FILE}' and set real values, or re-run with --force to proceed anyway (not recommended)." >&2
    exit 1
  fi
}

# =============================================================================
# Health / status helpers
# =============================================================================
wait_for_healthy() {
  local container="$1"
  local max_attempts=60
  local attempt=1
  echo "⏳ Waiting for '${container}' to become healthy..."
  until [ "$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null)" = "healthy" ]; do
    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "❌ Error: '${container}' did not become healthy after ${max_attempts} attempts." >&2
      "${DC_CMD[@]}" logs --tail=50 "$container" || true
      exit 1
    fi
    sleep 3
    attempt=$((attempt + 1))
  done
  echo "🟢 '${container}' is healthy."
}

print_status() {
  "${DC_CMD[@]}" ps
  echo ""
  echo "Container health:"
  local c health
  for c in "${ALL_CONTAINERS[@]}"; do
    health="$(docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null)" || true
    if [ -z "$health" ]; then
      health="not running"
    fi
    printf '  %-24s %s\n' "$c" "$health"
  done
}

# =============================================================================
# Commands
# =============================================================================
cmd_start() {
  validate_required_secrets

  echo "🚀 Building and starting production services (${COMPOSE_FILE})..."
  "${DC_CMD[@]}" up -d --build

  local c
  for c in "${ALL_CONTAINERS[@]}"; do
    wait_for_healthy "$c"
  done

  echo ""
  echo "✅ Production stack is up."
  echo "   API:      http://${API_HOST_BIND:-127.0.0.1}:${API_HOST_PORT:-8000}  (docs: /docs)"
  echo "   Keycloak: http://${KEYCLOAK_HOST_BIND:-127.0.0.1}:${KEYCLOAK_HOST_PORT:-8080}"
}

cmd_stop() {
  if [ -n "$SERVICE_ARG" ]; then
    echo "🛑 Stopping '${SERVICE_ARG}'..."
    "${DC_CMD[@]}" stop "$SERVICE_ARG"
  else
    echo "🛑 Stopping all production services (containers + volumes/network kept)..."
    "${DC_CMD[@]}" stop
  fi
}

cmd_restart() {
  local existing
  existing="$("${DC_CMD[@]}" ps -q)" || true
  if [ -z "$existing" ]; then
    echo "ℹ️  No containers currently exist for this project -- starting fresh instead." >&2
    cmd_start
    return
  fi
  if [ -n "$SERVICE_ARG" ]; then
    echo "🔄 Restarting '${SERVICE_ARG}'..."
    "${DC_CMD[@]}" restart "$SERVICE_ARG"
  else
    echo "🔄 Restarting all production services..."
    "${DC_CMD[@]}" restart
  fi
}

cmd_status() {
  print_status
}

cmd_logs() {
  if [ -n "$SERVICE_ARG" ]; then
    "${DC_CMD[@]}" logs -f --tail=200 "$SERVICE_ARG"
  else
    "${DC_CMD[@]}" logs -f --tail=200
  fi
}

cmd_build() {
  echo "🔨 Building images (${COMPOSE_FILE})..."
  "${DC_CMD[@]}" build
}

cmd_down() {
  echo "📦 Stopping and removing containers/network (named volumes customer360-pgdata / customer360-redisdata are kept)..."
  "${DC_CMD[@]}" down
}

cmd_seed_demo() {
  local cir_dir="${SCRIPT_DIR}/backend-system/identity_resolution"
  local venv_dir="${cir_dir}/.venv"
  local recreate_venv="0"

  if [ ! -d "$cir_dir" ]; then
    echo "❌ Error: '${cir_dir}' not found." >&2
    exit 1
  fi

  if [ ! -d "$venv_dir" ] || [ ! -x "$venv_dir/bin/python" ]; then
    recreate_venv="1"
  else
    local venv_prefix
    venv_prefix="$($venv_dir/bin/python -c 'import os, sys; print(os.path.realpath(sys.prefix))' 2>/dev/null || true)"
    if [ "$venv_prefix" != "$venv_dir" ]; then
      echo "♻️  Detected stale CIR virtual environment (prefix: ${venv_prefix}). Recreating..."
      recreate_venv="1"
    fi
  fi

  if [ "$recreate_venv" -eq 1 ]; then
    rm -rf "$venv_dir"
    echo "📦 Creating CIR virtual environment at ${venv_dir}..."
    python3 -m venv "$venv_dir"
  fi

  local venv_python="${venv_dir}/bin/python"

  echo "📥 Installing CIR requirements..."
  "$venv_python" -m pip install -q -r "${cir_dir}/requirements.txt"

  echo "🌱 Seeding base raw profiles for demo..."
  (cd "$cir_dir" && "$venv_python" scripts/init_sample_data.py)

  echo "⚙️  Running Customer Identity Resolution for demo..."
  (cd "$cir_dir" && "$venv_python" scripts/run_demo_resolution.py)

  echo "🌐 Seeding full POC/UAT demo data (including cdp_content_items)..."
  (cd "$cir_dir" && "$venv_python" scripts/seed_full_demo_data.py)

  echo "✅ POC/UAT demo data seeding complete."
}

# Every command below invokes 'docker compose', and docker-compose.yml
# requires DB_PASSWORD/REDIS_PASSWORD/KEYCLOAK_ADMIN_PASSWORD just to parse
# (${VAR:?...} guards) -- so .env must exist and be sourced first, regardless
# of which command was requested.
ensure_env_file
sync_env_keys
# shellcheck disable=SC1091
set -a
source "$ENV_FILE"
set +a

case "$COMMAND" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_restart ;;
  status|ps) cmd_status ;;
  logs) cmd_logs ;;
  build) cmd_build ;;
  down) cmd_down ;;
  seed-demo) cmd_seed_demo ;;
esac
