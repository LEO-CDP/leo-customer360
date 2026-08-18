#!/usr/bin/env bash
# Deploy Keycloak (SSO / OIDC) for customer360-api onto a VM over SSH.
#
#   uat  -> a Keycloak DOCKER container on the SAME VM as customer360-api
#           (shared box; server key "api"), command `start-dev`.
#   prod -> a Keycloak DOCKER container on a DEDICATED vServer (server key "sso"),
#           command `start` (production mode; HTTP behind the LB, TLS at the LB).
#
# Both use the managed PostgreSQL, database `db_keycloak` (already created by
# postgres/init/02-create-keycloak-db.sql). The target VM is discovered from the
# sibling ../server deployment's outputs by its for_each key.
#
# Usage:
#   ./deploy-sso.sh <uat|prod>            # (re)deploy Keycloak
#   ./deploy-sso.sh <uat|prod> destroy    # remove the container
#
# Secret: KEYCLOAK_ADMIN_PASSWORD from .env (git-ignored). The DB password is
# reused from ../postgres (terraform.tfvars / TF_VAR_db_password).
set -euo pipefail
cd "$(dirname "$0")"

ENV="${1:-}"; ACTION="${2:-deploy}"
case "$ENV" in uat | prod) ;; *) echo "Usage: ./deploy-sso.sh <uat|prod> [deploy|destroy]"; exit 1 ;; esac

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
SSO_SERVER_KEY="${SSO_SERVER_KEY:-$(tfval sso_server_key "$ovl")}"; SSO_SERVER_KEY="${SSO_SERVER_KEY:-api}"
KC_IMAGE="$(tfval keycloak_image "$ovl")"; KC_IMAGE="${KC_IMAGE:-keycloak/keycloak:26.7}"
KC_COMMAND="$(tfval keycloak_command "$ovl")"; KC_COMMAND="${KC_COMMAND:-start-dev}"
KC_HTTP_PORT="$(tfval keycloak_http_port "$ovl")"; KC_HTTP_PORT="${KC_HTTP_PORT:-8080}"
KC_ADMIN_USER="$(tfval keycloak_admin_user "$ovl")"; KC_ADMIN_USER="${KC_ADMIN_USER:-admin}"
KC_DB_NAME="$(tfval keycloak_db_name "$ovl")"; KC_DB_NAME="${KC_DB_NAME:-db_keycloak}"
KC_HOSTNAME_CFG="$(tfval keycloak_hostname "$ovl")"
JAVA_HEAP="$(tfval java_heap "$ovl")"

: "${KEYCLOAK_ADMIN_PASSWORD:?set KEYCLOAK_ADMIN_PASSWORD in .env (cp .env.example .env)}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"

# --- discover the target VM's public IP from ../server outputs (by for_each key) ---
SERVERS_JSON="$( (cd ../server && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -json servers 2>/dev/null) || true )"
[[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no ../server servers output for $ENV — deploy the server first."; exit 1; }
srv_ip() { printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get(sys.argv[2]) for i in (s.get("internal_interfaces") or []) if i.get(sys.argv[2])), ""))' "$1" "$2"; }
FIP="$(srv_ip "$SSO_SERVER_KEY" floating_ip)"
[[ -n "$FIP" ]] || { echo "ERROR: no floating IP for server key '$SSO_SERVER_KEY' — define it in ../server/overlays/$ENV.tfvars."; exit 1; }
BASTION="${BASTION_USER:-leocdp360}@$FIP"
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)

if [[ "$ACTION" == "destroy" ]]; then
  echo ">> Removing Keycloak container on $BASTION ..."
  ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo docker rm -f c360-keycloak >/dev/null 2>&1; echo "   removed"'
  exit 0
fi

# --- DB connection from the postgres deployment ---
pg="../postgres"
DB_USER="$(tfval db_username "$pg/overlays/$ENV.tfvars")"
DB_PASS="${TF_VAR_db_password:-$(tfval db_password "$pg/terraform.tfvars")}"
DB_HOST="$( (cd "$pg" && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -raw db_host 2>/dev/null) || true )"
DB_PORT="$( (cd "$pg" && terraform output -raw db_port 2>/dev/null) || echo 5432 )"
: "${DB_USER:?missing db_username}"; : "${DB_PASS:?missing db_password}"; : "${DB_HOST:?could not read db_host from ../postgres outputs}"

KC_HOSTNAME_EFF="${KC_HOSTNAME_CFG:-$FIP}" # default to the box's public IP if not configured
# keycloak_hostname may be a bare host/IP or a full URL (e.g. behind the LB on a non-standard port)
case "$KC_HOSTNAME_EFF" in http*) KC_URL="$KC_HOSTNAME_EFF" ;; *) KC_URL="http://$KC_HOSTNAME_EFF:$KC_HTTP_PORT" ;; esac
echo ">> $ENV: deploying Keycloak ($KC_IMAGE, $KC_COMMAND) on $BASTION"
echo "   DB   : jdbc:postgresql://$DB_HOST:$DB_PORT/$KC_DB_NAME (user $DB_USER)"
echo "   HTTP : $KC_URL   admin: $KC_ADMIN_USER"

# Ship secrets + the space-containing JAVA_HEAP base64-encoded: ssh flattens args
# into a remote shell string, so a raw value with spaces/# would corrupt the argv.
DBPW_B64="$(printf %s "$DB_PASS" | base64 | tr -d '\n')"
ADMPW_B64="$(printf %s "$KEYCLOAK_ADMIN_PASSWORD" | base64 | tr -d '\n')"
HEAP_B64="$(printf %s "$JAVA_HEAP" | base64 | tr -d '\n')"

ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' \
  "$KC_IMAGE" "$KC_COMMAND" "$KC_HTTP_PORT" "$DB_HOST" "$DB_PORT" "$KC_DB_NAME" "$DB_USER" "$DBPW_B64" \
  "$KC_ADMIN_USER" "$ADMPW_B64" "$KC_HOSTNAME_EFF" "$HEAP_B64" <<'REMOTE'
set -euo pipefail
IMG="$1"; CMD="$2"; PORT="$3"; DBHOST="$4"; DBPORT="$5"; DBNAME="$6"; DBUSER="$7"; DBPW="$(printf %s "$8" | base64 -d)"
ADMUSER="$9"; ADMPW="$(printf %s "${10}" | base64 -d)"; HOSTNAME_EFF="${11}"; JAVA_HEAP="$(printf %s "${12:-}" | base64 -d 2>/dev/null || true)"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  sudo systemctl enable --now docker
fi
sudo docker pull "$IMG" >/dev/null

# Keycloak 26: admin bootstrap via KC_BOOTSTRAP_ADMIN_*, health on the mgmt port (9000).
args=(
  -e KC_DB=postgres
  -e KC_DB_URL="jdbc:postgresql://$DBHOST:$DBPORT/$DBNAME"
  -e KC_DB_USERNAME="$DBUSER"
  -e KC_DB_PASSWORD="$DBPW"
  -e KC_HTTP_PORT="$PORT"
  -e KC_HEALTH_ENABLED=true
  -e KC_BOOTSTRAP_ADMIN_USERNAME="$ADMUSER"
  -e KC_BOOTSTRAP_ADMIN_PASSWORD="$ADMPW"
  -e KC_HOSTNAME="$HOSTNAME_EFF"
)
[ -n "$JAVA_HEAP" ] && args+=( -e JAVA_OPTS_APPEND="$JAVA_HEAP" )
# Production `start` behind an HTTP load balancer (TLS terminates at the LB).
if [ "$CMD" = "start" ]; then
  args+=( -e KC_HTTP_ENABLED=true -e KC_PROXY_HEADERS=xforwarded -e KC_HOSTNAME_STRICT=false )
fi

sudo docker rm -f c360-keycloak >/dev/null 2>&1 || true
sudo docker run -d --name c360-keycloak --restart unless-stopped --network host "${args[@]}" "$IMG" "$CMD"

echo "   waiting for Keycloak readiness (http://127.0.0.1:9000/health/ready) ..."
ok=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:9000/health/ready" >/dev/null 2>&1; then ok=1; break; fi
  sleep 3
done
sudo docker ps --filter name=c360-keycloak --format '   running: {{.Names}} ({{.Status}})'
case "$HOSTNAME_EFF" in http*) KC_URL="$HOSTNAME_EFF" ;; *) KC_URL="http://$HOSTNAME_EFF:$PORT" ;; esac
if [ "$ok" = "1" ]; then
  echo "   READY: Keycloak on $KC_URL (admin console at /admin, user $ADMUSER)"
else
  echo "   WARN: not ready within timeout — recent logs:"; sudo docker logs --tail 40 c360-keycloak || true; exit 1
fi
REMOTE
echo ">> Done. Point the API at it: set SSO_LOGIN=true + SSO_LOGIN_URL and re-run ../server/deploy-api.sh $ENV"
