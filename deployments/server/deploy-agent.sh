#!/usr/bin/env bash
# Deploy customer360-agent (AI Agent service, FastAPI :8009 -> LiteLLM) onto its OWN
# "agent" server VM. Same module-resident pattern as deploy-docs-search.sh: the box is
# servers["$AGENT_SERVER_KEY"] (default "agent") in overlays/<env>.tfvars, provisioned by
# this module's deploy.sh (apply).
#
#   ./deploy-agent.sh <uat|prod>            # (re)deploy the container
#   ./deploy-agent.sh <uat|prod> destroy    # remove the container
#
# CD path: PULLS the CI-built GHCR image (set BUILD_LOCAL=1 to build on the VM). The
# prompt-store schema + seed live in database-init (applied by ../postgres/run-sql.sh),
# so nothing DB-bootstrapping happens here.
#
# LLM (LiteLLM) config is env-var-with-defaults (OpenAI by default). Overrides (env):
#   BASTION_USER / SSH_KEY / AGENT_SERVER_KEY / AGENT_PORT / IMAGE_TAG / BUILD_LOCAL
#   LLM_MODEL / LLM_BASE_URL / LLM_EXTRA_CONFIG / AGENT_DB_SCHEMA / OTEL_ENABLED
#   LLM_API_KEY  (secret; else LEO_OPENAI_API_KEY, mirroring docs' DOCS_OPENAI_API_KEY)
# DB creds come from ../postgres (outputs + TF_VAR_db_password).
set -euo pipefail
cd "$(dirname "$0")"           # deployments/server (this IS the server TF module)
REPO_ROOT="$(cd ../.. && pwd)" # repo root (contains customer360-agent/)

ENV="${1:-}"; ACTION="${2:-deploy}"
case "$ENV" in uat | prod) ;; *) echo "Usage: ./deploy-agent.sh <uat|prod> [deploy|destroy]"; exit 1 ;; esac

# Keep CI-injected LLM config (secret key + model name) authoritative over an optional local server/.env.
_LEO_OPENAI_API_KEY_FROM_ENV="${LEO_OPENAI_API_KEY:-}"
_LEO_OPENAI_MODEL_NAME_FROM_ENV="${LEO_OPENAI_MODEL_NAME:-}"
_LLM_API_KEY_FROM_ENV="${LLM_API_KEY:-}"
_AGENT_API_TOKEN_FROM_ENV="${AGENT_API_TOKEN:-}"
[[ -f .env ]] && { set -a; source ./.env; set +a; }
[[ -n "$_LEO_OPENAI_API_KEY_FROM_ENV" ]] && LEO_OPENAI_API_KEY="$_LEO_OPENAI_API_KEY_FROM_ENV"
[[ -n "$_LEO_OPENAI_MODEL_NAME_FROM_ENV" ]] && LEO_OPENAI_MODEL_NAME="$_LEO_OPENAI_MODEL_NAME_FROM_ENV"
[[ -n "$_LLM_API_KEY_FROM_ENV" ]] && LLM_API_KEY="$_LLM_API_KEY_FROM_ENV"
[[ -n "$_AGENT_API_TOKEN_FROM_ENV" ]] && AGENT_API_TOKEN="$_AGENT_API_TOKEN_FROM_ENV"
unset _LEO_OPENAI_API_KEY_FROM_ENV _LEO_OPENAI_MODEL_NAME_FROM_ENV _LLM_API_KEY_FROM_ENV _AGENT_API_TOKEN_FROM_ENV

SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"
AGENT_SERVER_KEY="${AGENT_SERVER_KEY:-agent}"
AGENT_PORT="${AGENT_PORT:-8009}"
AGENT_DB_SCHEMA="${AGENT_DB_SCHEMA:-customer360}"
# Model: explicit LLM_MODEL wins; else the CD var LEO_OPENAI_MODEL_NAME (a bare name -> add the
# openai/ provider prefix LiteLLM needs, unless it already carries a provider/); else the default.
if [[ -z "${LLM_MODEL:-}" ]]; then
  if [[ -n "${LEO_OPENAI_MODEL_NAME:-}" ]]; then
    case "$LEO_OPENAI_MODEL_NAME" in */*) LLM_MODEL="$LEO_OPENAI_MODEL_NAME" ;; *) LLM_MODEL="openai/$LEO_OPENAI_MODEL_NAME" ;; esac
  else
    LLM_MODEL="openai/gpt-4o-mini"
  fi
fi
LLM_BASE_URL="${LLM_BASE_URL:-https://api.openai.com/v1}" # OpenAI endpoint
LLM_EXTRA_CONFIG="${LLM_EXTRA_CONFIG:-}"
# Secret key (never committed): explicit LLM_API_KEY wins, else LEO_OPENAI_API_KEY (CD secret).
LLM_API_KEY="${LLM_API_KEY:-${LEO_OPENAI_API_KEY:-}}"
# Shared bearer token gating /plan/* — must match customer360-api's AGENT_API_TOKEN.
AGENT_API_TOKEN="${AGENT_API_TOKEN:-}"

# Read a tfvars value: quoted-string content, or a bare token with a trailing comment stripped.
tfval() {
  local line; line="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" 2>/dev/null | head -1)"
  case "$line" in
    *\"*\"*) line="${line#*\"}"; printf '%s' "${line%%\"*}" ;;
    *) line="${line#*=}"; line="${line%%#*}"; printf '%s' "$(printf '%s' "$line" | tr -d '[:space:]')" ;;
  esac
}

# --- resolve the target VM by map key from THIS module's outputs ---
terraform workspace select "$ENV" >/dev/null 2>&1 || { echo "ERROR: no '$ENV' server workspace — deploy the server first."; exit 1; }
SERVERS_JSON="$(terraform output -json servers 2>/dev/null || true)"
[[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no servers output."; exit 1; }
srv_ip() { printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get(sys.argv[2]) for i in (s.get("internal_interfaces") or []) if i.get(sys.argv[2])), ""))' "$1" "$2"; }
FIP="$(srv_ip "$AGENT_SERVER_KEY" floating_ip)"
# The 'agent' box is provisioned by this module (overlays servers map). If it isn't there yet,
# SKIP rather than fail — so CD stays green until the box is applied; the next run picks it up.
[[ -n "$FIP" ]] || { echo ">> SKIP: no floating IP for server key '$AGENT_SERVER_KEY' — add the 'agent' box to overlays/$ENV.tfvars + ./deploy.sh $ENV apply."; exit 0; }
BASTION="${BASTION_USER:-leocdp360}@$FIP"
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)

if [[ "$ACTION" == "destroy" ]]; then
  echo ">> Removing customer360-agent container on $BASTION ..."
  ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo docker rm -f customer360-agent >/dev/null 2>&1; echo "   removed"'
  exit 0
fi

# --- Postgres prompt-store URL from the postgres deployment ---
pg="../postgres"
DB_NAME="$(tfval db_name "$pg/overlays/$ENV.tfvars")"
DB_USER="$(tfval db_username "$pg/overlays/$ENV.tfvars")"
DB_PASS="${TF_VAR_db_password:-$(tfval db_password "$pg/terraform.tfvars")}"
DB_HOST="$( (cd "$pg" && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -raw db_host 2>/dev/null) || true )"
DB_PORT="$( (cd "$pg" && terraform output -raw db_port 2>/dev/null) || echo 5432 )"
: "${DB_NAME:?missing db_name}"; : "${DB_USER:?missing db_username}"; : "${DB_PASS:?missing db_password}"; : "${DB_HOST:?could not read db_host from ../postgres outputs}"
AGENT_DATABASE_URL="postgresql+psycopg2://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}"

# A hosted LLM (OpenAI/Gemini/Anthropic default endpoints, or no custom base_url) needs a key;
# a local runtime (custom base_url) doesn't. Warn only for the former.
if [[ -z "$LLM_API_KEY" ]] && { [[ -z "$LLM_BASE_URL" ]] || [[ "$LLM_BASE_URL" == *api.openai.com* ]] || [[ "$LLM_BASE_URL" == *googleapis.com* ]] || [[ "$LLM_BASE_URL" == *anthropic* ]]; }; then
  echo ">> WARN: LLM_API_KEY is empty for a hosted LLM ($LLM_MODEL @ ${LLM_BASE_URL:-provider default}) — planning will 502 until it is set (LEO_OPENAI_API_KEY secret / ./.env / inline)."
fi
# Required on BOTH uat + prod (this script only runs for those): refuse to deploy an
# unauthenticated agent. (The app still allows a blank token for LOCAL dev.)
[[ -n "$AGENT_API_TOKEN" ]] || { echo "ERROR: AGENT_API_TOKEN is required for $ENV — set the AGENT_API_TOKEN secret (same value on customer360-api). Refusing to deploy an unauthenticated agent."; exit 1; }

echo ">> Target (agent): $BASTION :$AGENT_PORT   model: $LLM_MODEL   DB(prompt store): ${DB_NAME}.${AGENT_DB_SCHEMA}@${DB_HOST}:${DB_PORT}"

# --- CD image source: pull the CI-built image from GHCR by default; BUILD_LOCAL=1 builds on the VM. ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/ghcr.sh"
SERVICE="customer360-agent"
GHCR_USER="${GHCR_USER:-${GITHUB_ACTOR:-token}}"
GHCR_TOKEN="${GHCR_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ "${BUILD_LOCAL:-0}" == "1" ]]; then
  DEPLOY_MODE="build"; IMAGE=""
  echo ">> Image: BUILD_LOCAL=1 — building $SERVICE on the VM from source."
  tar -C "$REPO_ROOT" -czf - customer360-agent \
    | ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo mkdir -p /opt/c360 && sudo chown "$(id -un)" /opt/c360 && tar -C /opt/c360 -xzf -'
else
  DEPLOY_MODE="ghcr"
  IMAGE="$(image_ref "$SERVICE" "$(resolve_tag "overlays/$ENV.tfvars")")"
  echo ">> Image: $IMAGE   (pull from GHCR; BUILD_LOCAL=1 to build on the VM)"
fi

# --- OpenTelemetry tracing (OTLP -> Jaeger on the monitoring box). The agent runs on its OWN
#     box (never co-located with Jaeger), so resolve the mon box's PRIVATE ip. UAT off / PROD 10%
#     (lib/otel.sh); OTEL_ENABLED overrides. Open :4318 on the mon box from this box. ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/otel.sh"
MON_SERVER_KEY="${MON_SERVER_KEY:-$(tfval mon_server_key ../monitoring/overlays/$ENV.tfvars)}"; MON_SERVER_KEY="${MON_SERVER_KEY:-api}"
JAEGER_HOST="$(srv_ip "$MON_SERVER_KEY" fixed_ip)"; [[ -n "$JAEGER_HOST" ]] || JAEGER_HOST="127.0.0.1"
OTEL_LINES="$(otel_env_lines customer360-agent "$ENV" "$JAEGER_HOST")"

# env file built locally, shipped base64 (dodges ssh arg-flattening).
# Emit LLM_EXTRA_CONFIG only when set — pydantic-settings JSON-parses this dict field at
# the source level, so an empty value crashes startup (SettingsError, not a validator).
EXTRA_LINE=""; [[ -n "$LLM_EXTRA_CONFIG" ]] && EXTRA_LINE="LLM_EXTRA_CONFIG=$LLM_EXTRA_CONFIG"
ENVB64="$(printf '%s' "AGENT_DATABASE_URL=$AGENT_DATABASE_URL
AGENT_DB_SCHEMA=$AGENT_DB_SCHEMA
AGENT_API_TOKEN=$AGENT_API_TOKEN
AGENT_ROOT_PATH=${AGENT_ROOT_PATH:-/agent}
LLM_MODEL=$LLM_MODEL
LLM_API_KEY=$LLM_API_KEY
LLM_BASE_URL=$LLM_BASE_URL
$EXTRA_LINE
$OTEL_LINES" | base64 | tr -d '\n')"

echo ">> Deploying the container ..."
ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$AGENT_PORT" "$ENVB64" "$DEPLOY_MODE" "$IMAGE" "$GHCR_USER" "$(printf %s "$GHCR_TOKEN" | base64 | tr -d '\n')" < <(declare -f docker_pull_retry; cat <<'REMOTE'
set -euo pipefail
PORT="$1"; ENVB64="$2"; DEPLOY_MODE="${3:-build}"; IMAGE="${4:-}"; GHCR_USER="${5:-token}"; GHCR_TOKEN="$(printf %s "${6:-}" | base64 -d 2>/dev/null || true)"
command -v docker >/dev/null 2>&1 || { sudo apt-get update -qq; sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io; sudo systemctl enable --now docker; }

umask 077; env_file="$(mktemp)"; printf '%s' "$ENVB64" | base64 -d > "$env_file"
sudo mkdir -p /opt/c360; sudo mv "$env_file" /opt/c360/agent.env; sudo chmod 600 /opt/c360/agent.env
if [ "$DEPLOY_MODE" = "ghcr" ]; then
  echo "   pulling $IMAGE ..."
  [ -n "$GHCR_TOKEN" ] && printf %s "$GHCR_TOKEN" | sudo docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
  docker_pull_retry "$IMAGE"
  RUN_IMG="$IMAGE"
else
  sed -i 's/ --mount=[^ ]*//g' /opt/c360/customer360-agent/Dockerfile   # docker.io: no buildx
  sudo docker build -t customer360-agent-local /opt/c360/customer360-agent
  RUN_IMG="customer360-agent-local"
fi
sudo docker rm -f customer360-agent >/dev/null 2>&1 || true
sudo docker run -d --name customer360-agent --restart unless-stopped --network host --env-file /opt/c360/agent.env "$RUN_IMG"
sleep 3
curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && echo "   health OK (:$PORT/health)" || echo "   WARN: health not ready yet"
sudo docker ps --filter name=customer360-agent --format '   running: {{.Names}} ({{.Status}})'
REMOTE
)
echo ">> Done. Point customer360-api at this box (AGENT_SERVICE_URL=http://<agent-box-private-ip>:$AGENT_PORT) and open $AGENT_PORT to the api box."

# --- release ledger (best-effort) ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/record_deploy.sh"
record_deployment "$ENV" "$SERVICE" "${IMAGE:-local}" success
