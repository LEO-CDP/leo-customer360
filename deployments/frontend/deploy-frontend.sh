#!/usr/bin/env bash
# Deploy frontend-admin (FastAPI admin UI) onto a server VM over SSH.
#
#   uat  -> container on the api box (shared; server key "api") — the browser-facing
#           "web tier" that already runs the API + Keycloak; the backend box is full.
#   prod -> container on a DEDICATED vServer (server key "frontend").
#
# The frontend has NO secrets: it only serves the UI and injects non-secret config
# (the PUBLIC API URL + SSO flag). The browser — not this server — calls the API and
# Keycloak, so the only dependency is that both are reachable via the LB (they are).
#
#   ./deploy-frontend.sh <uat|prod>            # (re)deploy
#   ./deploy-frontend.sh <uat|prod> destroy    # remove the container
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(cd ../.. && pwd)" # repo root (contains frontend-admin/)

ENV="${1:-}"; ACTION="${2:-deploy}"
case "$ENV" in uat | prod) ;; *) echo "Usage: ./deploy-frontend.sh <uat|prod> [deploy|destroy]"; exit 1 ;; esac

[[ -f .env ]] && { set -a; source ./.env; set +a; }
# Read a tfvars value: content between quotes for strings (keeps '#'), or the bare
# token with any trailing comment stripped for unquoted numbers/bools.
tfval() {
  local line; line="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" 2>/dev/null | head -1)"
  case "$line" in
    *\"*\"*) line="${line#*\"}"; printf '%s' "${line%%\"*}" ;;
    *) line="${line#*=}"; line="${line%%#*}"; printf '%s' "$(printf '%s' "$line" | tr -d '[:space:]')" ;;
  esac
}

ovl="overlays/${ENV}.tfvars"
[[ -f "$ovl" ]] || { echo "ERROR: overlay $ovl not found."; exit 1; }
SRV_KEY="${FRONTEND_SERVER_KEY:-$(tfval frontend_server_key "$ovl")}"; SRV_KEY="${SRV_KEY:-api}"
PORT="$(tfval frontend_port "$ovl")"; PORT="${PORT:-8890}"
API_HOSTNAME="$(tfval frontend_api_hostname "$ovl")"
ROOT_PATH="$(tfval frontend_root_path "$ovl")" # empty = serve at the LB root (no proxy prefix)
TENANT="$(tfval frontend_tenant_id "$ovl")"; TENANT="${TENANT:-11111111-1111-1111-1111-111111111111}"
SSO_LOGIN="$(tfval sso_login "$ovl")"; SSO_LOGIN="${SSO_LOGIN:-false}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"
: "${API_HOSTNAME:?set frontend_api_hostname in $ovl (the PUBLIC API URL the browser uses)}"

# --- discover the target VM's public IP from ../server outputs (by for_each key) ---
SERVERS_JSON="$( (cd ../server && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -json servers 2>/dev/null) || true )"
[[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no ../server servers output for $ENV — deploy the server first."; exit 1; }
FIP="$(printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get("floating_ip") for i in (s.get("internal_interfaces") or []) if i.get("floating_ip")), ""))' "$SRV_KEY")"
[[ -n "$FIP" ]] || { echo "ERROR: no floating IP for server key '$SRV_KEY' — define it in ../server/overlays/$ENV.tfvars."; exit 1; }
BASTION="${BASTION_USER:-leocdp360}@$FIP"
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)

if [[ "$ACTION" == "destroy" ]]; then
  echo ">> Removing frontend container on $BASTION ..."
  ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo docker rm -f customer360-frontend >/dev/null 2>&1; echo "   removed"'
  exit 0
fi

echo ">> Target (frontend): $BASTION :$PORT"
echo "   API=$API_HOSTNAME  SSO_LOGIN=$SSO_LOGIN  tenant=$TENANT  root_path='${ROOT_PATH}'"

# --- CD image source: pull the CI-built image from GHCR by default; set
#     BUILD_LOCAL=1 to fall back to shipping source + building on the VM. ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/ghcr.sh"
SERVICE="frontend-admin"
GHCR_USER="${GHCR_USER:-${GITHUB_ACTOR:-token}}"
GHCR_TOKEN="${GHCR_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ "${BUILD_LOCAL:-0}" == "1" ]]; then
  DEPLOY_MODE="build"; IMAGE=""
  echo ">> Image: BUILD_LOCAL=1 — building $SERVICE on the VM from source."
  echo ">> Shipping frontend-admin/ ..."
  tar -C "$REPO_ROOT" -czf - frontend-admin \
    | ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo mkdir -p /opt/c360 && sudo chown "$(id -un)" /opt/c360 && tar -C /opt/c360 -xzf -'
else
  DEPLOY_MODE="ghcr"
  IMAGE="$(image_ref "$SERVICE" "$(resolve_tag "overlays/$ENV.tfvars")")"
  echo ">> Image: $IMAGE   (pull from GHCR; BUILD_LOCAL=1 to build on the VM)"
fi

# Build the env file locally and ship it base64-encoded as ONE arg (avoids the
# ssh arg-flattening trap where an empty/space value corrupts positional args).
ENV_B64="$(printf '%s' "SSO_LOGIN=$SSO_LOGIN
FRONTEND_API_HOSTNAME=$API_HOSTNAME
FRONTEND_TENANT_ID=$TENANT
FRONTEND_ROOT_PATH=$ROOT_PATH
HOST=0.0.0.0
PORT=$PORT" | base64 | tr -d '\n')"

echo ">> Building + (re)starting the container ..."
ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$PORT" "$ENV_B64" "$DEPLOY_MODE" "$IMAGE" "$GHCR_USER" "$(printf %s "$GHCR_TOKEN" | base64 | tr -d '\n')" <<'REMOTE'
set -euo pipefail
PORT="$1"; ENV_B64="$2"
DEPLOY_MODE="${3:-build}"; IMAGE="${4:-}"; GHCR_USER="${5:-token}"; GHCR_TOKEN="$(printf %s "${6:-}" | base64 -d 2>/dev/null || true)"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  sudo systemctl enable --now docker
fi
umask 077
env_file="$(mktemp)"
printf '%s' "$ENV_B64" | base64 -d > "$env_file"
sudo mkdir -p /opt/c360
sudo mv "$env_file" /opt/c360/frontend.env
sudo chmod 600 /opt/c360/frontend.env
if [ "$DEPLOY_MODE" = "ghcr" ]; then
  echo "   pulling $IMAGE ..."
  [ -n "$GHCR_TOKEN" ] && printf %s "$GHCR_TOKEN" | sudo docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
  sudo docker pull "$IMAGE"
  RUN_IMG="$IMAGE"
else
  # docker.io has no buildx -> strip the BuildKit `RUN --mount` (pip-cache only).
  sed -i 's/ --mount=[^ ]*//g' /opt/c360/frontend-admin/Dockerfile
  sudo docker build -t customer360-frontend /opt/c360/frontend-admin
  RUN_IMG="customer360-frontend"
fi
sudo docker rm -f customer360-frontend >/dev/null 2>&1 || true
sudo docker run -d --name customer360-frontend --restart unless-stopped --network host --env-file /opt/c360/frontend.env "$RUN_IMG"
sleep 3
curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && echo "   health OK (:$PORT/health)" || echo "   WARN: health not ready yet"
sudo docker ps --filter name=customer360-frontend --format '   running: {{.Names}} ({{.Status}})'
REMOTE
echo ">> Done. Expose it via the LB (add a 'frontend' backend -> <box-ip>:$PORT), then open http://<lb>:$PORT/"
