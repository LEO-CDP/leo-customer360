#!/usr/bin/env bash
# Deploy the Caddy reverse proxy onto a server VM over SSH. Caddy terminates TLS
# (auto Let’s Encrypt) and path-routes ONE public host (caddy_domain) to the
# co-located containers on the api box (and the backend box for dagster).
#
#   ./deploy-caddy.sh <uat|prod>            # (re)deploy Caddy (issues/renews certs)
#   ./deploy-caddy.sh <uat|prod> validate   # adapt+validate the Caddyfile on the box (no deploy, no ACME)
#   ./deploy-caddy.sh <uat|prod> destroy    # remove the Caddy container (cert volume kept)
#
#   uat  -> the SHARED api box (server key "api"); prod -> set caddy_server_key.
#
# Prereqs before a real deploy (see README — this is a CUTOVER step):
#   * DNS: caddy_domain (A record) -> the LB public IP, else ACME HTTP-01 can't issue.
#   * LB : forward :80 AND :443 -> this box (Caddy needs :80 for the ACME challenge
#          + the HTTP->HTTPS redirect, and :443 for traffic).
set -euo pipefail
cd "$(dirname "$0")"

ENV="${1:-}"; ACTION="${2:-deploy}"
case "$ENV" in uat | prod) ;; *) echo "Usage: ./deploy-caddy.sh <uat|prod> [deploy|validate|destroy]"; exit 1 ;; esac

[[ -f .env ]] && { set -a; source ./.env; set +a; }
SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"

# Read a tfvars value: content between quotes for strings, or the bare token with any
# trailing comment stripped for unquoted numbers/bools.
tfval() {
  local line; line="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" 2>/dev/null | head -1)"
  case "$line" in
    *\"*\"*) line="${line#*\"}"; printf '%s' "${line%%\"*}" ;;
    *) line="${line#*=}"; line="${line%%#*}"; printf '%s' "$(printf '%s' "$line" | tr -d '[:space:]')" ;;
  esac
}

ovl="overlays/${ENV}.tfvars"
[[ -f "$ovl" ]] || { echo "ERROR: overlay $ovl not found."; exit 1; }

CADDY_SERVER_KEY="${CADDY_SERVER_KEY:-$(tfval caddy_server_key "$ovl")}"; CADDY_SERVER_KEY="${CADDY_SERVER_KEY:-api}"
DOMAIN="$(tfval caddy_domain "$ovl")"
EMAIL="$(tfval acme_email "$ovl")"
IMG="$(tfval caddy_image "$ovl")";           IMG="${IMG:-caddy:2-alpine}"
API_UP="$(tfval api_upstream "$ovl")";       API_UP="${API_UP:-127.0.0.1:8008}"
KC_UP="$(tfval keycloak_upstream "$ovl")";   KC_UP="${KC_UP:-127.0.0.1:8080}"
FE_UP="$(tfval frontend_upstream "$ovl")";   FE_UP="${FE_UP:-127.0.0.1:8890}"
ADS_UP="$(tfval ads_upstream "$ovl")";       ADS_UP="${ADS_UP:-127.0.0.1:9009}"
DAG_UP="$(tfval dagster_upstream "$ovl")";   DAG_UP="${DAG_UP:-10.100.1.4:3000}"
NET_UP="$(tfval netdata_upstream "$ovl")";   NET_UP="${NET_UP:-127.0.0.1:4199}"
PORT_UP="$(tfval portainer_upstream "$ovl")";PORT_UP="${PORT_UP:-127.0.0.1:9443}"

: "${DOMAIN:?set caddy_domain in $ovl (e.g. cdp.example.com)}"
: "${EMAIL:?set acme_email in $ovl (Let’s Encrypt account email)}"

# --- discover the target VM's floating IP from ../server outputs (by for_each key) ---
SERVERS_JSON="$( (cd ../server && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -json servers 2>/dev/null) || true )"
[[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no ../server servers output for $ENV — deploy the server first."; exit 1; }
FIP="$(printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get("floating_ip") for i in (s.get("internal_interfaces") or []) if i.get("floating_ip")), ""))' "$CADDY_SERVER_KEY")"
[[ -n "$FIP" ]] || { echo "ERROR: no floating IP for server key '$CADDY_SERVER_KEY' — define it in ../server/overlays/$ENV.tfvars."; exit 1; }
BASTION="${BASTION_USER:-leocdp360}@$FIP"
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)

if [[ "$ACTION" == "destroy" ]]; then
  echo ">> Removing Caddy on $BASTION ..."
  ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo docker rm -f c360-caddy >/dev/null 2>&1; echo "   removed c360-caddy (cert volume caddy_data kept)"'
  exit 0
fi

CADDYFILE_B64="$(base64 < Caddyfile | tr -d '\n')"
# All params in one base64 blob (dodges ssh arg-flattening; values are space-free).
PARAMS_B64="$(printf '%s\n' \
  "ACTION=$ACTION" "IMG=$IMG" "DOMAIN=$DOMAIN" "EMAIL=$EMAIL" \
  "API_UP=$API_UP" "KC_UP=$KC_UP" "FE_UP=$FE_UP" "ADS_UP=$ADS_UP" \
  "DAG_UP=$DAG_UP" "NET_UP=$NET_UP" "PORT_UP=$PORT_UP" \
  "CADDYFILE_B64=$CADDYFILE_B64" | base64 | tr -d '\n')"

echo ">> Target (caddy): $BASTION   [$ACTION]"
echo "   domain: https://$DOMAIN   image: $IMG"
echo "   routes: / -> $FE_UP   /c360api -> $API_UP   /auth -> $KC_UP   /ads -> $ADS_UP"

ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$PARAMS_B64" <<'REMOTE'
set -euo pipefail
tmp="$(mktemp)"; printf %s "$1" | base64 -d > "$tmp"; set -a; . "$tmp"; set +a; rm -f "$tmp"

if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  sudo systemctl enable --now docker
fi

sudo mkdir -p /opt/c360/caddy
printf %s "$CADDYFILE_B64" | base64 -d | sudo tee /opt/c360/caddy/Caddyfile >/dev/null

# env the Caddyfile placeholders resolve against
env_args=(
  -e CADDY_DOMAIN="$DOMAIN" -e ACME_EMAIL="$EMAIL"
  -e API_UPSTREAM="$API_UP" -e KC_UPSTREAM="$KC_UP" -e FRONTEND_UPSTREAM="$FE_UP"
  -e ADS_UPSTREAM="$ADS_UP" -e DAGSTER_UPSTREAM="$DAG_UP"
  -e NETDATA_UPSTREAM="$NET_UP" -e PORTAINER_UPSTREAM="$PORT_UP"
)
sudo docker pull "$IMG" >/dev/null || true

# --- validate: adapt+check the Caddyfile with the real env, then stop (no ACME, no bind) ---
echo "   validating Caddyfile ..."
sudo docker run --rm "${env_args[@]}" -v /opt/c360/caddy/Caddyfile:/etc/caddy/Caddyfile:ro \
  "$IMG" caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile
echo "   Caddyfile OK"
[ "$ACTION" = "validate" ] && exit 0

# --- deploy: run Caddy on host net so it binds :80/:443 and reaches 127.0.0.1:<svc> + the backend box ---
sudo docker volume create caddy_data >/dev/null   # certs/ACME account — MUST persist across restarts
sudo docker volume create caddy_config >/dev/null
sudo docker rm -f c360-caddy >/dev/null 2>&1 || true
sudo docker run -d --name c360-caddy --restart unless-stopped --network host \
  "${env_args[@]}" \
  -v /opt/c360/caddy/Caddyfile:/etc/caddy/Caddyfile:ro \
  -v caddy_data:/data -v caddy_config:/config \
  "$IMG"

ok=0; for _ in $(seq 1 15); do curl -fsS -o /dev/null "http://127.0.0.1:80" 2>/dev/null && { ok=1; break; }; sleep 2; done
sudo docker ps --filter name=c360-caddy --format '   running: {{.Names}} ({{.Status}})'
if [ "$ok" = "1" ]; then
  echo "   Caddy is serving on :80 (HTTP->HTTPS redirect + ACME challenge)."
else
  echo "   NOTE: :80 not answering a bare request yet — check logs. Certs need DNS + LB :80/:443 -> this box:"
fi
echo "   --- recent Caddy logs (cert status) ---"
sudo docker logs --tail 20 c360-caddy 2>&1 | sed 's/^/     /' || true
REMOTE

echo ">> Done."
if [[ "$ACTION" == "deploy" ]]; then
  echo "   Caddy issues a Let’s Encrypt cert for $DOMAIN once (a) DNS resolves to the LB and"
  echo "   (b) the LB forwards :80 AND :443 to this box. Until then it runs but serves no HTTPS."
  echo "   Point the LB (../load_balancer/overlays/$ENV.tfvars) at caddy :80/:443, then:"
  echo "     (cd ../load_balancer && ./deploy.sh $ENV apply)"
fi
