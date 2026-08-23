#!/usr/bin/env bash
# Deploy the monitoring stack onto a server VM over SSH — everything driven by
# overlays/<env>.tfvars, in ONE script:
#
#   Portainer  -> container-ops dashboard (logs/exec/restart/CPU-mem). Bridge net, HTTPS,
#                 bound LOOPBACK-only (127.0.0.1:9443); reachable via the SSH tunnel or the
#                 SSO gate below.
#   Netdata    -> real-time metrics dashboard (host + per-container + Redis). host net, :19999.
#   pgAdmin    -> web Postgres admin/monitoring UI (dpage/pgadmin4). Own login (email+password),
#                 GATED behind Keycloak SSO (pgadmin_sso=true) since it serves cleartext HTTP. Bridge net.
#   oauth2-proxy (optional, oauth2_enabled) -> a Keycloak SSO gate in front of the
#                 dashboards, so they can be exposed publicly through the L4 LB (which can't
#                 do OIDC itself). One confidential client `c360-oauth2-proxy` in the
#                 existing customer360 realm; one proxy container per enabled dashboard.
#
#     browser -> LB :<dash_port> (TCP) -> box :<proxy_port> oauth2-proxy -> [Keycloak] -> 127.0.0.1:<dash_port>
#
#   uat  -> everything on the SHARED api box (server key "api").
#   prod -> api box by default; set mon_server_key to a dedicated box in overlays/prod.tfvars.
#
#   ./deploy-monitoring.sh <uat|prod>            # (re)deploy the enabled pieces
#   ./deploy-monitoring.sh <uat|prod> destroy    # remove all monitoring containers (volumes kept)
#
# Secrets (.env, git-ignored) — all OPTIONAL / auto-managed:
#   PORTAINER_ADMIN_PASSWORD    — >=12 chars; bootstraps Portainer's admin non-interactively.
#   OAUTH2_PROXY_CLIENT_SECRET  — from Keycloak; auto-provisioned by bootstrap-oauth2-client.py
#                                 (needs KEYCLOAK_ADMIN_PASSWORD; reused from ../sso/.env if unset).
#   OAUTH2_PROXY_COOKIE_SECRET  — session-cookie key; auto-generated once if missing.
#
# Dashboard ports dodge the api box's in-use ports (redis 6580, api 8008, keycloak 8080 +
# 9000, frontend 8890, ads 9009). Do NOT move Portainer onto :9000 — Keycloak owns it.
set -euo pipefail
cd "$(dirname "$0")"

ENV="${1:-}"; ACTION="${2:-deploy}"
case "$ENV" in uat | prod) ;; *) echo "Usage: ./deploy-monitoring.sh <uat|prod> [deploy|destroy]"; exit 1 ;; esac

[[ -f .env ]] && { set -a; source ./.env; set +a; }
# Reuse the Keycloak admin password from the sibling sso deployment if not set locally.
[[ -z "${KEYCLOAK_ADMIN_PASSWORD:-}" && -f ../sso/.env ]] && { set -a; source ../sso/.env; set +a; }
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
MON_SERVER_KEY="${MON_SERVER_KEY:-$(tfval mon_server_key "$ovl")}"; MON_SERVER_KEY="${MON_SERVER_KEY:-api}"
# dashboards
P_EN="$(tfval portainer_enabled "$ovl")";  P_EN="${P_EN:-true}"
P_PORT="$(tfval portainer_port "$ovl")";   P_PORT="${P_PORT:-9443}"
P_IMG="$(tfval portainer_image "$ovl")";   P_IMG="${P_IMG:-portainer/portainer-ce:lts}"
N_EN="$(tfval netdata_enabled "$ovl")";    N_EN="${N_EN:-true}"
N_PORT="$(tfval netdata_port "$ovl")";     N_PORT="${N_PORT:-19999}"   # fixed at Netdata's default; see README to change
N_IMG="$(tfval netdata_image "$ovl")";     N_IMG="${N_IMG:-netdata/netdata:stable}"
# jaeger — OpenTelemetry OTLP trace backend + UI (added for API request tracing).
# Default OFF: on uat it stays off (tiny box, profile on demand); prod overlay flips it on.
J_EN="$(tfval jaeger_enabled "$ovl")";               J_EN="${J_EN:-false}"
J_IMG="$(tfval jaeger_image "$ovl")";                J_IMG="${J_IMG:-jaegertracing/all-in-one:1.62.0}"
J_UI_PORT="$(tfval jaeger_ui_port "$ovl")";          J_UI_PORT="${J_UI_PORT:-16686}"
J_UI_BIND="$(tfval jaeger_ui_bind "$ovl")";          J_UI_BIND="${J_UI_BIND:-127.0.0.1}"
J_OTLP_HTTP="$(tfval jaeger_otlp_http_port "$ovl")"; J_OTLP_HTTP="${J_OTLP_HTTP:-4318}"
J_OTLP_GRPC="$(tfval jaeger_otlp_grpc_port "$ovl")"; J_OTLP_GRPC="${J_OTLP_GRPC:-4317}"
J_MEM="$(tfval jaeger_mem "$ovl")";                  J_MEM="${J_MEM:-300m}"
# pgadmin — web Postgres admin/monitoring UI (dpage/pgadmin4). Own login (email+password), NOT
# gated by default (pgadmin_sso=false); TUNNEL-ONLY (loopback) since it serves cleartext HTTP.
# Default OFF (heaviest of the four ~150-250 MB; flip on to inspect the DB — see README).
PG_EN="$(tfval pgadmin_enabled "$ovl")";   PG_EN="${PG_EN:-false}"
PG_PORT="$(tfval pgadmin_port "$ovl")";    PG_PORT="${PG_PORT:-5050}"
PG_IMG="$(tfval pgadmin_image "$ovl")";    PG_IMG="${PG_IMG:-dpage/pgadmin4:8.14}"
PG_MEM="$(tfval pgadmin_mem "$ovl")";      PG_MEM="${PG_MEM:-512m}"
# Login email: from .env (PGADMIN_DEFAULT_EMAIL) or the overlay (pgadmin_email); the password
# lives ONLY in .env (auto-generated + persisted below if unset).
PG_EMAIL="${PGADMIN_DEFAULT_EMAIL:-$(tfval pgadmin_email "$ovl")}"; PG_EMAIL="${PG_EMAIL:-admin@leocdp.com}"
# portainer agents — manage OTHER boxes' Docker from the SAME Portainer (one pane of glass, one
# login) instead of a second Portainer. Comma-separated ../server keys to run portainer/agent on
# and auto-register as Portainer environments (e.g. "1x2" = the backend/Dagster box). Portainer
# reaches each agent over the PRIVATE VPC on :PA_PORT — that port must be open on the box's
# secgroup from the Portainer box's private IP (deployments/server agent_ports; applied out-of-band).
PA_KEYS="$(tfval portainer_agent_server_keys "$ovl")"
PA_IMG="$(tfval portainer_agent_image "$ovl")";  PA_IMG="${PA_IMG:-portainer/agent:lts}"
PA_PORT="$(tfval portainer_agent_port "$ovl")";  PA_PORT="${PA_PORT:-9001}"
# sso gate
OA_EN="$(tfval oauth2_enabled "$ovl")";    OA_EN="${OA_EN:-false}"
OA_IMG="$(tfval oauth2_image "$ovl")";     OA_IMG="${OA_IMG:-quay.io/oauth2-proxy/oauth2-proxy:v7.6.0}"
ISSUER="$(tfval oauth2_issuer_url "$ovl")"
CLIENT_ID="$(tfval oauth2_client_id "$ovl")"; CLIENT_ID="${CLIENT_ID:-c360-oauth2-proxy}"
PUB_HOST="$(tfval oauth2_public_host "$ovl")"
P_PROXY="$(tfval portainer_proxy_port "$ovl")"; P_PROXY="${P_PROXY:-4443}"
N_PROXY="$(tfval netdata_proxy_port "$ovl")";   N_PROXY="${N_PROXY:-4199}"
J_PROXY="$(tfval jaeger_proxy_port "$ovl")";    J_PROXY="${J_PROXY:-4686}"
PG_PROXY="$(tfval pgadmin_proxy_port "$ovl")";  PG_PROXY="${PG_PROXY:-4050}"
# Per-dashboard SSO gating. Portainer has its OWN login AND a CSRF/origin check that rejects
# mutating requests behind a reverse proxy ("Forbidden - origin invalid"), so it is exposed
# DIRECTLY (portainer_sso=false). Netdata has no auth of its own, so keep it gated.
P_SSO="$(tfval portainer_sso "$ovl")"; P_SSO="${P_SSO:-true}"
N_SSO="$(tfval netdata_sso "$ovl")";   N_SSO="${N_SSO:-true}"
J_SSO="$(tfval jaeger_sso "$ovl")";    J_SSO="${J_SSO:-true}"   # Jaeger has NO native auth -> gate it like Netdata
PG_SSO="$(tfval pgadmin_sso "$ovl")";  PG_SSO="${PG_SSO:-false}"  # pgAdmin has its OWN login -> direct like Portainer
P_GATED=false; [[ "$OA_EN" == "true" && "$P_EN" == "true" && "$P_SSO" == "true" ]] && P_GATED=true
N_GATED=false; [[ "$OA_EN" == "true" && "$N_EN" == "true" && "$N_SSO" == "true" ]] && N_GATED=true
J_GATED=false; [[ "$OA_EN" == "true" && "$J_EN" == "true" && "$J_SSO" == "true" ]] && J_GATED=true
PG_GATED=false; [[ "$OA_EN" == "true" && "$PG_EN" == "true" && "$PG_SSO" == "true" ]] && PG_GATED=true
# Gated dashboards bind loopback (only the proxy reaches them); an un-gated but enabled
# dashboard binds all interfaces so the LB can reach it directly.
P_BIND="127.0.0.1"; [[ "$P_GATED" == "true" ]] || P_BIND="0.0.0.0"
# pgAdmin is a DB admin tool: default LOOPBACK (reach via the admin SSH tunnel), because on
# the L4 LB uat rides plain HTTP and pgAdmin serves HTTP (no self-signed TLS like Portainer)
# — a public :5050 would ship the admin login in cleartext. Set pgadmin_bind="0.0.0.0" to
# expose it directly to the LB anyway (accept the cleartext caveat), or gate it (pgadmin_sso).
PG_BIND="$(tfval pgadmin_bind "$ovl")"; PG_BIND="${PG_BIND:-127.0.0.1}"
[[ "$PG_GATED" == "true" ]] && PG_BIND="127.0.0.1"   # gated -> only the oauth2-proxy reaches it

[[ "$P_EN" == "true" || "$N_EN" == "true" || "$J_EN" == "true" || "$PG_EN" == "true" ]] || { echo "ERROR: portainer_enabled, netdata_enabled, jaeger_enabled and pgadmin_enabled are all false in $ovl — nothing to do."; exit 1; }

# --- discover the target VM's floating IP from ../server outputs (by for_each key) ---
SERVERS_JSON="$( (cd ../server && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -json servers 2>/dev/null) || true )"
[[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no ../server servers output for $ENV — deploy the server first."; exit 1; }
FIP="$(printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get("floating_ip") for i in (s.get("internal_interfaces") or []) if i.get("floating_ip")), ""))' "$MON_SERVER_KEY")"
[[ -n "$FIP" ]] || { echo "ERROR: no floating IP for server key '$MON_SERVER_KEY' — define it in ../server/overlays/$ENV.tfvars."; exit 1; }
BASTION="${BASTION_USER:-leocdp360}@$FIP"
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)

if [[ "$ACTION" == "destroy" ]]; then
  echo ">> Removing monitoring containers on $BASTION ..."
  ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo docker rm -f c360-portainer c360-netdata c360-jaeger c360-pgadmin c360-oauth2-portainer c360-oauth2-netdata c360-oauth2-jaeger c360-oauth2-pgadmin >/dev/null 2>&1; echo "   removed dashboards + Jaeger + pgAdmin + SSO gate (data volumes kept)"'
  exit 0
fi

# --- optional Portainer admin bootstrap password ---
if [[ -n "${PORTAINER_ADMIN_PASSWORD:-}" && ${#PORTAINER_ADMIN_PASSWORD} -lt 12 ]]; then
  echo "ERROR: PORTAINER_ADMIN_PASSWORD must be >= 12 chars (Portainer rejects shorter)."; exit 1
fi
PADMIN_B64="$(printf %s "${PORTAINER_ADMIN_PASSWORD:-}" | base64 | tr -d '\n')"

# --- pgAdmin admin login (the image REQUIRES a password to start when enabled) ---
# Auto-generate + persist to .env if unset (same convenience as the cookie secret), so a
# first deploy just works and the credential is recorded once in the git-ignored .env.
if [[ "$PG_EN" == "true" ]]; then
  if [[ -z "${PGADMIN_DEFAULT_PASSWORD:-}" ]]; then
    # openssl (finite output) not `tr </dev/urandom | head` — the latter SIGPIPEs tr on the
    # infinite source, which under `set -o pipefail` returns 141 and set -e kills the script.
    PGADMIN_DEFAULT_PASSWORD="$(openssl rand -base64 24 | LC_ALL=C tr -dc 'A-Za-z0-9')"
    printf 'PGADMIN_DEFAULT_PASSWORD=%s\n' "$PGADMIN_DEFAULT_PASSWORD" >> ./.env
    echo ">> Generated PGADMIN_DEFAULT_PASSWORD (saved to .env) — log in at pgAdmin as $PG_EMAIL."
  elif [[ ${#PGADMIN_DEFAULT_PASSWORD} -lt 6 ]]; then
    echo "ERROR: PGADMIN_DEFAULT_PASSWORD must be >= 6 chars (pgAdmin rejects shorter)."; exit 1
  fi
fi
PGADMIN_PW_B64="$(printf %s "${PGADMIN_DEFAULT_PASSWORD:-}" | base64 | tr -d '\n')"

# --- SSO gate: provision the Keycloak client + secrets locally (before we ship) ---
SEC_B64=""; COOKIE_B64=""; P_REDIRECT=""; N_REDIRECT=""; J_REDIRECT=""; PG_REDIRECT=""
if [[ "$P_GATED" == "true" || "$N_GATED" == "true" || "$J_GATED" == "true" || "$PG_GATED" == "true" ]]; then
  : "${ISSUER:?set oauth2_issuer_url in $ovl (e.g. http://<lb>:8080/realms/customer360)}"
  : "${PUB_HOST:?set oauth2_public_host in $ovl (the LB public IP/host the browser uses)}"
  KC_URL="${ISSUER%/realms/*}"; REALM="${ISSUER##*/realms/}"
  P_REDIRECT="http://$PUB_HOST:$P_PORT/oauth2/callback"
  N_REDIRECT="http://$PUB_HOST:$N_PORT/oauth2/callback"
  J_REDIRECT="https://$PUB_HOST/jaeger/oauth2/callback"
  PG_REDIRECT="http://$PUB_HOST:$PG_PORT/oauth2/callback"   # pgAdmin gated on its own port, root-served (like Netdata)
  # only GATED dashboards get a callback URL registered on the Keycloak client
  REDIRECTS=""; [[ "$P_GATED" == "true" ]] && REDIRECTS="$P_REDIRECT"; [[ "$N_GATED" == "true" ]] && REDIRECTS="${REDIRECTS:+$REDIRECTS,}$N_REDIRECT"; [[ "$J_GATED" == "true" ]] && REDIRECTS="${REDIRECTS:+$REDIRECTS,}$J_REDIRECT"; [[ "$PG_GATED" == "true" ]] && REDIRECTS="${REDIRECTS:+$REDIRECTS,}$PG_REDIRECT"

  if [[ -z "${OAUTH2_PROXY_CLIENT_SECRET:-}" ]]; then
    : "${KEYCLOAK_ADMIN_PASSWORD:?need the Keycloak client secret — set KEYCLOAK_ADMIN_PASSWORD in .env (or ../sso/.env) so bootstrap-oauth2-client.py can provision it}"
    echo ">> Provisioning Keycloak client '$CLIENT_ID' in realm '$REALM' ..."
    KC_URL="$KC_URL" REALM="$REALM" CLIENT_ID="$CLIENT_ID" REDIRECT_URIS="$REDIRECTS" \
      KEYCLOAK_ADMIN_PASSWORD="$KEYCLOAK_ADMIN_PASSWORD" python3 bootstrap-oauth2-client.py
    set -a; source ./.env; set +a
  fi
  : "${OAUTH2_PROXY_CLIENT_SECRET:?client secret still empty after bootstrap — check Keycloak}"

  if [[ -z "${OAUTH2_PROXY_COOKIE_SECRET:-}" ]]; then
    OAUTH2_PROXY_COOKIE_SECRET="$(openssl rand -base64 32)"
    printf 'OAUTH2_PROXY_COOKIE_SECRET=%s\n' "$OAUTH2_PROXY_COOKIE_SECRET" >> ./.env
    echo ">> Generated OAUTH2_PROXY_COOKIE_SECRET (saved to .env)."
  fi
  SEC_B64="$(printf %s "$OAUTH2_PROXY_CLIENT_SECRET" | base64 | tr -d '\n')"
  COOKIE_B64="$(printf %s "$OAUTH2_PROXY_COOKIE_SECRET" | base64 | tr -d '\n')"
fi

echo ">> Target (monitoring): $BASTION"
[[ "$P_EN" == "true" ]] && echo "   Portainer : $P_BIND:$P_PORT  ($P_IMG)  [$([[ "$P_GATED" == "true" ]] && echo "SSO gate" || echo "direct, own login")]"
[[ "$N_EN" == "true" ]] && echo "   Netdata   : :$N_PORT  ($N_IMG)  [$([[ "$N_GATED" == "true" ]] && echo "SSO gate" || echo "DIRECT, no auth")]"
[[ "$J_EN" == "true" ]] && echo "   Jaeger    : $J_UI_BIND:$J_UI_PORT (UI)  OTLP http :$J_OTLP_HTTP grpc :$J_OTLP_GRPC  ($J_IMG)"
[[ "$PG_EN" == "true" ]] && echo "   pgAdmin   : $PG_BIND:$PG_PORT  ($PG_IMG)  [$([[ "$PG_GATED" == "true" ]] && echo "SSO gate + own login" || echo "direct, own login")]  login=$PG_EMAIL"
[[ "$P_GATED" == "true" || "$N_GATED" == "true" || "$J_GATED" == "true" || "$PG_GATED" == "true" ]] && echo "   SSO gate  : oauth2-proxy -> Keycloak ($ISSUER), client $CLIENT_ID"

# All params shipped in one base64 blob (dodges ssh arg-flattening; values are space-free).
PARAMS_B64="$(printf '%s\n' \
  "P_EN=$P_EN" "P_PORT=$P_PORT" "P_IMG=$P_IMG" "PADMIN_B64=$PADMIN_B64" "P_BIND=$P_BIND" \
  "P_GATED=$P_GATED" "N_GATED=$N_GATED" "J_GATED=$J_GATED" "PG_GATED=$PG_GATED" \
  "N_EN=$N_EN" "N_PORT=$N_PORT" "N_IMG=$N_IMG" \
  "J_EN=$J_EN" "J_IMG=$J_IMG" "J_UI_PORT=$J_UI_PORT" "J_UI_BIND=$J_UI_BIND" "J_OTLP_HTTP=$J_OTLP_HTTP" "J_OTLP_GRPC=$J_OTLP_GRPC" "J_MEM=$J_MEM" \
  "PG_EN=$PG_EN" "PG_PORT=$PG_PORT" "PG_IMG=$PG_IMG" "PG_BIND=$PG_BIND" "PG_MEM=$PG_MEM" "PG_EMAIL=$PG_EMAIL" "PGADMIN_PW_B64=$PGADMIN_PW_B64" \
  "OA_EN=$OA_EN" "OA_IMG=$OA_IMG" "ISSUER=$ISSUER" "CLIENT_ID=$CLIENT_ID" \
  "SEC_B64=$SEC_B64" "COOKIE_B64=$COOKIE_B64" \
  "P_PROXY=$P_PROXY" "P_REDIRECT=$P_REDIRECT" "N_PROXY=$N_PROXY" "N_REDIRECT=$N_REDIRECT" "J_PROXY=$J_PROXY" "J_REDIRECT=$J_REDIRECT" "PG_PROXY=$PG_PROXY" "PG_REDIRECT=$PG_REDIRECT" \
  | base64 | tr -d '\n')"

ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$PARAMS_B64" <<'REMOTE'
set -euo pipefail
tmp="$(mktemp)"; printf %s "$1" | base64 -d > "$tmp"; set -a; . "$tmp"; set +a; rm -f "$tmp"
PADMIN="$(printf %s "$PADMIN_B64" | base64 -d 2>/dev/null || true)"
PGADMIN_PW="$(printf %s "$PGADMIN_PW_B64" | base64 -d 2>/dev/null || true)"
SECRET="$(printf %s "$SEC_B64" | base64 -d 2>/dev/null || true)"
COOKIE="$(printf %s "$COOKIE_B64" | base64 -d 2>/dev/null || true)"

if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  sudo systemctl enable --now docker
fi

# ---------- Portainer (container ops UI, loopback-only) ----------
if [ "$P_EN" = "true" ]; then
  echo "   deploying Portainer ..."
  sudo docker pull "$P_IMG" >/dev/null || true
  sudo docker volume create portainer_data >/dev/null
  sudo docker rm -f c360-portainer >/dev/null 2>&1 || true
  run_args=(
    -d --name c360-portainer --restart unless-stopped
    -p "$P_BIND":"$P_PORT":9443   # gated -> 127.0.0.1 (proxy/tunnel only); direct -> 0.0.0.0 so the LB can reach it
    -v /var/run/docker.sock:/var/run/docker.sock
    -v portainer_data:/data
  )
  # -H auto-connects the local Docker env. Without it, the --admin-password-file bootstrap
  # skips Portainer's setup wizard — the step that would otherwise create the local
  # environment — so the UI shows an admin but NO containers/images.
  cmd_args=( -H unix:///var/run/docker.sock )
  if [ -n "$PADMIN" ]; then
    pwf="/opt/c360/portainer_admin_pw"; sudo mkdir -p /opt/c360
    printf '%s' "$PADMIN" | sudo tee "$pwf" >/dev/null; sudo chmod 600 "$pwf"
    run_args+=( -v "$pwf":/run/portainer_pw:ro ); cmd_args+=( --admin-password-file /run/portainer_pw )
  fi
  sudo docker run "${run_args[@]}" "$P_IMG" "${cmd_args[@]}"
  ok=0; for _ in $(seq 1 20); do curl -fsSk "https://127.0.0.1:$P_PORT/api/status" >/dev/null 2>&1 && { ok=1; break; }; sleep 2; done
  sudo docker ps --filter name=c360-portainer --format '   running: {{.Names}} ({{.Status}})'
  [ "$ok" = "1" ] && echo "   Portainer OK" || echo "   WARN: Portainer not ready yet"
fi

# ---------- Netdata (real-time metrics UI) ----------
if [ "$N_EN" = "true" ]; then
  echo "   deploying Netdata ..."
  sudo docker pull "$N_IMG" >/dev/null || true
  sudo docker volume create netdataconfig >/dev/null; sudo docker volume create netdatalib >/dev/null; sudo docker volume create netdatacache >/dev/null
  sudo docker rm -f c360-netdata >/dev/null 2>&1 || true
  sudo docker run -d --name c360-netdata --restart unless-stopped \
    --pid host --network host \
    -v netdataconfig:/etc/netdata -v netdatalib:/var/lib/netdata -v netdatacache:/var/cache/netdata \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    -v /proc:/host/proc:ro -v /sys:/host/sys:ro -v /etc/os-release:/host/etc/os-release:ro \
    -v /etc/passwd:/host/etc/passwd:ro -v /etc/group:/host/etc/group:ro \
    --cap-add SYS_PTRACE --cap-add SYS_ADMIN --security-opt apparmor=unconfined \
    "$N_IMG"
  ok=0; for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:$N_PORT/api/v1/info" >/dev/null 2>&1 && { ok=1; break; }; sleep 2; done
  sudo docker ps --filter name=c360-netdata --format '   running: {{.Names}} ({{.Status}})'
  [ "$ok" = "1" ] && echo "   Netdata OK" || echo "   WARN: Netdata not ready yet"
fi

# ---------- Jaeger (OTLP trace backend + UI, badger on-disk storage) ----------
# OTLP receiver on 4317/4318 (COLLECTOR_OTLP_ENABLED); traces persisted to a badger
# volume (low RAM vs in-memory). UI bound per J_UI_BIND (loopback by default -> reach
# via the SSH tunnel); OTLP ports published on 0.0.0.0 so per-service prod boxes reach
# them over the private VPC. Memory-capped so it can't starve the shared uat box.
if [ "${J_EN:-false}" = "true" ]; then
  echo "   deploying Jaeger (all-in-one, badger storage) ..."
  sudo docker pull "$J_IMG" >/dev/null || true
  sudo docker volume create jaeger_data >/dev/null
  sudo docker rm -f c360-jaeger >/dev/null 2>&1 || true
  sudo docker run -d --name c360-jaeger --restart unless-stopped --user root --memory "${J_MEM:-300m}" -e COLLECTOR_OTLP_ENABLED=true -e QUERY_BASE_PATH=/jaeger -e SPAN_STORAGE_TYPE=badger -e BADGER_EPHEMERAL=false -e BADGER_DIRECTORY_VALUE=/badger/data -e BADGER_DIRECTORY_KEY=/badger/key -v jaeger_data:/badger -p "${J_UI_BIND:-127.0.0.1}:${J_UI_PORT:-16686}:16686" -p "0.0.0.0:${J_OTLP_HTTP:-4318}:4318" "$J_IMG"   # gRPC 4317 NOT published: Netdata's otel-plugin owns host :4317; apps export OTLP/HTTP :4318
  ok=0; for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:${J_UI_PORT:-16686}/" >/dev/null 2>&1 && { ok=1; break; }; sleep 2; done
  sudo docker ps --filter name=c360-jaeger --format '   running: {{.Names}} ({{.Status}})'
  [ "$ok" = "1" ] && echo "   Jaeger OK (UI :${J_UI_PORT:-16686}, OTLP http :${J_OTLP_HTTP:-4318} (gRPC in-container))" || echo "   WARN: Jaeger not ready yet"
fi

# ---------- pgAdmin (web Postgres admin/monitoring UI, own login) ----------
# Bridge net, container listens on :80 -> published on PG_BIND:PG_PORT (loopback by default =
# tunnel-only; set pgadmin_bind=0.0.0.0 to expose to the LB). Login = PG_EMAIL / PGADMIN_PW.
# pgadmin_data volume persists the config DB + saved server connections + sessions. Memory
# capped so it can't starve the shared box (it's the heaviest of the four).
if [ "${PG_EN:-false}" = "true" ]; then
  echo "   deploying pgAdmin ..."
  sudo docker pull "$PG_IMG" >/dev/null || true
  sudo docker volume create pgadmin_data >/dev/null
  sudo docker rm -f c360-pgadmin >/dev/null 2>&1 || true
  # GUNICORN_CMD_ARGS raises gunicorn's per-field header limit (default 8190B): all these ops
  # tools share one host/IP, and cookies are host- not port-scoped, so the big _oauth2_* session
  # cookies oauth2-proxy sets for Netdata/Jaeger get sent to pgAdmin too -> the combined Cookie:
  # header overflows the default and gunicorn returns 431 "limit request headers fields size".
  sudo docker run -d --name c360-pgadmin --restart unless-stopped --memory "${PG_MEM:-512m}" \
    -e PGADMIN_DEFAULT_EMAIL="$PG_EMAIL" -e PGADMIN_DEFAULT_PASSWORD="$PGADMIN_PW" \
    -e PGADMIN_LISTEN_PORT=80 \
    -e GUNICORN_CMD_ARGS="--limit-request-field_size 65535 --limit-request-fields 200" \
    -v pgadmin_data:/var/lib/pgadmin \
    -p "$PG_BIND":"$PG_PORT":80 \
    "$PG_IMG"
  ok=0; for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:$PG_PORT/misc/ping" >/dev/null 2>&1 && { ok=1; break; }; sleep 2; done
  sudo docker ps --filter name=c360-pgadmin --format '   running: {{.Names}} ({{.Status}})'
  [ "$ok" = "1" ] && echo "   pgAdmin OK (login $PG_EMAIL on :$PG_PORT)" || { echo "   WARN: pgAdmin not ready — logs:"; sudo docker logs --tail 25 c360-pgadmin || true; }
fi

# ---------- oauth2-proxy (Keycloak SSO gate, one per GATED dashboard) ----------
# Tear down any gate that should no longer exist (e.g. Portainer moved to direct access).
[ "$P_GATED" = "true" ]  || sudo docker rm -f c360-oauth2-portainer >/dev/null 2>&1 || true
[ "$N_GATED" = "true" ]  || sudo docker rm -f c360-oauth2-netdata   >/dev/null 2>&1 || true
[ "$J_GATED" = "true" ]  || sudo docker rm -f c360-oauth2-jaeger    >/dev/null 2>&1 || true
[ "$PG_GATED" = "true" ] || sudo docker rm -f c360-oauth2-pgadmin   >/dev/null 2>&1 || true
if [ "$P_GATED" = "true" ] || [ "$N_GATED" = "true" ] || [ "$J_GATED" = "true" ] || [ "$PG_GATED" = "true" ]; then
  sudo docker pull "$OA_IMG" >/dev/null || true
  run_proxy() {  # name listen redirect upstream [cookie_secure] [proxy_prefix] [extra_flag]
    local name="$1" listen="$2" redirect="$3" upstream="$4" cookie_secure="${5:-false}" pprefix="${6:-}" extra="${7:-}"
    local args=(
      --provider=oidc --oidc-issuer-url="$ISSUER" --client-id="$CLIENT_ID" --client-secret="$SECRET"
      --cookie-secret="$COOKIE" --cookie-name="_oauth2_$name" --cookie-secure="$cookie_secure"
      --email-domain="*" --insecure-oidc-allow-unverified-email=true
      --http-address="0.0.0.0:$listen" --redirect-url="$redirect" --upstream="$upstream"
      --reverse-proxy=true --skip-provider-button=true
    )
    [ -n "$pprefix" ] && args+=( "--proxy-prefix=$pprefix" )
    [ -n "$extra" ] && args+=( "$extra" )
    sudo docker rm -f "c360-oauth2-$name" >/dev/null 2>&1 || true
    sudo docker run -d --name "c360-oauth2-$name" --restart unless-stopped --network host "$OA_IMG" "${args[@]}"
    local ok=0; for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:$listen/ping" >/dev/null 2>&1 && { ok=1; break; }; sleep 2; done
    sudo docker ps --filter "name=c360-oauth2-$name" --format '   running: {{.Names}} ({{.Status}})'
    [ "$ok" = "1" ] && echo "   oauth2-$name OK (/ping on :$listen)" || { echo "   WARN: oauth2-$name not ready — logs:"; sudo docker logs --tail 25 "c360-oauth2-$name" || true; }
  }
  if [ "$P_GATED" = "true" ]; then echo "   deploying oauth2-proxy for Portainer ..."; run_proxy portainer "$P_PROXY" "$P_REDIRECT" "https://127.0.0.1:$P_PORT" false "" "--ssl-upstream-insecure-skip-verify=true"; fi
  if [ "$N_GATED" = "true" ]; then echo "   deploying oauth2-proxy for Netdata ...";   run_proxy netdata   "$N_PROXY" "$N_REDIRECT" "http://127.0.0.1:$N_PORT" false "" ""; fi
  if [ "$J_GATED" = "true" ]; then echo "   deploying oauth2-proxy for Jaeger ...";    run_proxy jaeger    "$J_PROXY" "$J_REDIRECT" "http://127.0.0.1:$J_UI_PORT" true "/jaeger/oauth2" ""; fi
  # pgAdmin is root-served on its own port (like Netdata), so no proxy-prefix needed.
  if [ "$PG_GATED" = "true" ]; then echo "   deploying oauth2-proxy for pgAdmin ...";   run_proxy pgadmin   "$PG_PROXY" "$PG_REDIRECT" "http://127.0.0.1:$PG_PORT" false "" ""; fi
fi
REMOTE

# ---------- Portainer agents on OTHER boxes (one Portainer, many environments) ----------
# For each ../server key in PA_KEYS: run portainer/agent on that box (reached via its floating IP)
# and register it in the api-box Portainer as an Agent environment (reached over the private VPC
# at <fixed_ip>:PA_PORT). Idempotent; registration is best-effort (falls back to a manual hint).
if [[ "$P_EN" == "true" && -n "$PA_KEYS" ]]; then
  _ip_of() { printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get(sys.argv[2]) for i in (s.get("internal_interfaces") or []) if i.get(sys.argv[2])), ""))' "$1" "$2"; }
  IFS=',' read -ra _PA_KEYS <<< "$PA_KEYS"
  for key in "${_PA_KEYS[@]}"; do
    key="$(printf '%s' "$key" | tr -d '[:space:]')"; [[ -n "$key" ]] || continue
    afip="$(_ip_of "$key" floating_ip)"; apriv="$(_ip_of "$key" fixed_ip)"
    [[ -n "$afip" && -n "$apriv" ]] || { echo "   WARN: no floating/private IP for agent server key '$key' — skipping"; continue; }
    echo ">> Portainer agent on '$key'  (private $apriv:$PA_PORT, ssh $afip)  ($PA_IMG)"
    ssh "${SSH_OPTS[@]}" "${BASTION_USER:-leocdp360}@$afip" 'bash -s' "$PA_IMG" "$PA_PORT" <<'AREMOTE'
set -eu
IMG="$1"; PORT="$2"
command -v docker >/dev/null 2>&1 || { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io && sudo systemctl enable --now docker; }
sudo docker pull "$IMG" >/dev/null || true
sudo docker rm -f c360-portainer-agent >/dev/null 2>&1 || true
sudo docker run -d --name c360-portainer-agent --restart unless-stopped \
  -p "$PORT":9001 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/lib/docker/volumes:/var/lib/docker/volumes \
  "$IMG" >/dev/null
sudo docker ps --filter name=c360-portainer-agent --format '   agent: {{.Names}} ({{.Status}})'
AREMOTE
    # register the environment in Portainer (run ON the mon box against its loopback API)
    if [[ -n "${PORTAINER_ADMIN_PASSWORD:-}" ]]; then
      ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$P_PORT" "$(printf %s "$PORTAINER_ADMIN_PASSWORD" | base64 | tr -d '\n')" "c360-$key" "$apriv:$PA_PORT" <<'RREMOTE'
set -eu
PPORT="$1"; PW="$(printf %s "$2" | base64 -d)"; NAME="$3"; URL="$4"; base="https://127.0.0.1:$PPORT"
jwt="$(curl -sk -X POST "$base/api/auth" -H 'Content-Type: application/json' -d "{\"Username\":\"admin\",\"Password\":\"$PW\"}" | sed -n 's/.*"jwt":"\([^"]*\)".*/\1/p')"
[ -n "$jwt" ] || { echo "   WARN: Portainer auth failed — register manually: Environments -> Add -> Agent -> $URL"; exit 0; }
if curl -sk "$base/api/endpoints" -H "Authorization: Bearer $jwt" | grep -q "\"Name\":\"$NAME\""; then
  echo "   Portainer env '$NAME': already registered"
else
  # Agent endpoints need the tcp:// scheme, else Portainer 500s with "Unable to parse docker host".
  code="$(curl -sk -o /dev/null -w '%{http_code}' -X POST "$base/api/endpoints" -H "Authorization: Bearer $jwt" \
    -F "Name=$NAME" -F "EndpointCreationType=2" -F "URL=tcp://$URL" -F "TLS=true" -F "TLSSkipVerify=true" -F "TLSSkipClientVerify=true")"
  case "$code" in 200|201|204) echo "   Portainer env '$NAME' -> $URL: registered";;
    *) echo "   WARN: endpoint create HTTP $code — add manually: Environments -> Add -> Agent -> $URL";; esac
fi
RREMOTE
    else
      echo "   NOTE: PORTAINER_ADMIN_PASSWORD not in .env — add the env in the UI: Environments -> Add -> Agent -> $apriv:$PA_PORT"
    fi
  done
fi

echo ">> Done."
echo "   Apply/refresh the LB backends (in ../load_balancer/overlays/$ENV.tfvars):"
echo "     (cd ../load_balancer && ./deploy.sh $ENV apply)"
if [[ "$P_EN" == "true" && -n "$PUB_HOST" ]]; then
  [[ "$P_GATED" == "true" ]] \
    && echo "   Portainer: http://$PUB_HOST:$P_PORT/   (Keycloak gate + Portainer login)" \
    || echo "   Portainer: https://$PUB_HOST:$P_PORT/  (Portainer's OWN login; self-signed TLS, LB -> :$P_PORT direct)"
fi
if [[ "$N_EN" == "true" && -n "$PUB_HOST" ]]; then
  [[ "$N_GATED" == "true" ]] \
    && echo "   Netdata  : http://$PUB_HOST:$N_PORT/   (Keycloak login)" \
    || echo "   Netdata  : http://$PUB_HOST:$N_PORT/   (DIRECT — NO AUTH; set netdata_sso=true)"
fi
if [[ "$J_EN" == "true" && -n "$PUB_HOST" ]]; then
  [[ "$J_GATED" == "true" ]] \
    && echo "   Jaeger   : https://$PUB_HOST/jaeger  (trace UI, Keycloak login)" \
    || echo "   Jaeger   : $J_UI_BIND:$J_UI_PORT (tunnel only — set jaeger_sso=true + oauth2_enabled + a Caddy /jaeger route to gate over TLS)"
fi
if [[ "$PG_EN" == "true" ]]; then
  if [[ "$PG_GATED" == "true" && -n "$PUB_HOST" ]]; then
    echo "   pgAdmin  : http://$PUB_HOST:$PG_PORT/  (Keycloak gate + pgAdmin login $PG_EMAIL; add a 'pgadmin' LB backend -> :$PG_PROXY)"
  elif [[ "$PG_BIND" == "0.0.0.0" && -n "$PUB_HOST" ]]; then
    echo "   pgAdmin  : http://$PUB_HOST:$PG_PORT/  (pgAdmin's OWN login $PG_EMAIL; add a 'pgadmin' LB backend -> :$PG_PORT — CLEARTEXT)"
  else
    echo "   pgAdmin  : $PG_BIND:$PG_PORT (login $PG_EMAIL — tunnel-only; see the admin tunnel below)"
  fi
fi
echo "   Admin tunnel (no LB): ssh -i $SSH_KEY -L $P_PORT:localhost:$P_PORT -L $N_PORT:localhost:$N_PORT${PG_EN:+ -L $PG_PORT:localhost:$PG_PORT} $BASTION"
