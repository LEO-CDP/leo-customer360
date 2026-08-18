#!/usr/bin/env bash
# Deploy backend-system (the Dagster orchestrator) onto the server VM for an env.
#   ./deploy-backend.sh <uat|prod>
#
# Ships the repo's backend-system/ to the box over SSH (tar-over-ssh — no git creds
# needed on the VM), installs Docker if missing, builds the image, and (re)runs it as a
# container on port 3000 with --network host so it reaches the PRIVATE customer360 DB
# (same subnet as the VM). Re-runnable: it rebuilds and replaces the container.
#
# SSH target + key are auto-discovered from THIS deployment's outputs (the floating IP)
# and ~/.ssh/c360-api_ed25519; override via BASTION_USER / SSH_KEY / BASTION.
# DB connection is read from the sibling ../postgres deployment.
set -euo pipefail
cd "$(dirname "$0")"                 # deployments/server
REPO_ROOT="$(cd ../.. && pwd)"       # repo root (contains backend-system/)

ENV="${1:-}"
case "$ENV" in
  uat | prod) ;;
  *) echo "Usage: ./deploy-backend.sh <uat|prod>"; exit 1 ;;
esac

[[ -f .env ]] && { set -a; source ./.env; set +a; }
SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"
tfval() { grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" 2>/dev/null | sed -E 's/.*"([^"]+)".*/\1/' | head -1; }

# --- SSH target: the BACKEND server's floating IP (selected by map key) ---
BACKEND_SERVER_KEY="${BACKEND_SERVER_KEY:-1x2}"
if [[ -z "${BASTION:-}" ]]; then
  terraform workspace select "$ENV" >/dev/null 2>&1 || { echo "ERROR: no '$ENV' server workspace — deploy the server first (./deploy.sh $ENV apply)."; exit 1; }
  SERVERS_JSON="$(terraform output -json servers 2>/dev/null || true)"
  [[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no servers output."; exit 1; }
  FIP="$(printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get("floating_ip") for i in (s.get("internal_interfaces") or []) if i.get("floating_ip")), ""))' "$BACKEND_SERVER_KEY")"
  [[ -n "$FIP" ]] || { echo "ERROR: no floating IP for server key '$BACKEND_SERVER_KEY' (set BACKEND_SERVER_KEY)."; exit 1; }
  BASTION="${BASTION_USER:-leocdp360}@$FIP"
fi
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
echo ">> Target: $BASTION"

# --- DB connection from the postgres deployment ---
pg="../postgres"
DB_NAME="$(tfval db_name "$pg/overlays/$ENV.tfvars")"
DB_USER="$(tfval db_username "$pg/overlays/$ENV.tfvars")"
DB_PASS="${TF_VAR_db_password:-$(tfval db_password "$pg/terraform.tfvars")}"
DB_HOST="$( (cd "$pg" && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -raw db_host 2>/dev/null) || true )"
DB_PORT="$( (cd "$pg" && terraform output -raw db_port 2>/dev/null) || echo 5432 )"
: "${DB_NAME:?missing db_name}"; : "${DB_USER:?missing db_username}"; : "${DB_PASS:?missing db_password}"; : "${DB_HOST:?could not read db_host from ../postgres outputs (is it applied?)}"
echo ">> DB: ${DB_NAME}@${DB_HOST}:${DB_PORT} (user ${DB_USER})"

# --- ship backend-system/ to the VM ---
echo ">> Shipping backend-system/ ..."
tar -C "$REPO_ROOT" -czf - backend-system \
  | ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo mkdir -p /opt/c360 && sudo chown "$(id -un)" /opt/c360 && tar -C /opt/c360 -xzf -'

# --- build + run on the VM (values passed as positional args; password base64'd) ---
echo ">> Installing Docker (if needed), building, and (re)starting the container ..."
PW_B64="$(printf %s "$DB_PASS" | base64 | tr -d '\n')"
ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$DB_HOST" "$DB_PORT" "$DB_NAME" "$DB_USER" "$PW_B64" <<'REMOTE'
set -euo pipefail
DB_HOST="$1"; DB_PORT="$2"; DB_NAME="$3"; DB_USER="$4"; DB_PW="$(printf %s "$5" | base64 -d)"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  sudo systemctl enable --now docker
fi
umask 077
env_file="$(mktemp)"
cat > "$env_file" <<ENVF
DB_HOST=$DB_HOST
DB_PORT=$DB_PORT
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PW
DB_SCHEMA=$DB_NAME
ENVF
sudo mkdir -p /opt/c360
sudo mv "$env_file" /opt/c360/backend.env
sudo chmod 600 /opt/c360/backend.env
echo "   building image (this can take a few minutes on a small box)..."
sudo docker build -t backend-system /opt/c360/backend-system
sudo docker rm -f backend-system >/dev/null 2>&1 || true
sudo docker run -d --name backend-system --restart unless-stopped --network host --env-file /opt/c360/backend.env backend-system
sleep 3
sudo docker ps --filter name=backend-system --format '   running: {{.Names}} ({{.Status}}) image={{.Image}}'
REMOTE

echo ">> Done. Dagster UI is on the VM at :3000. Reach it from your laptop with an SSH tunnel:"
echo "   ssh -i $SSH_KEY -L 3000:localhost:3000 $BASTION   # then open http://localhost:3000"
