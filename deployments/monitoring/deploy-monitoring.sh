#!/usr/bin/env bash
# Deploy the monitoring stack onto a server VM over SSH — everything driven by
# overlays/<env>.tfvars, in ONE script:
#
#   Portainer  -> container-ops dashboard (logs/exec/restart/CPU-mem). Bridge net, HTTPS,
#                 bound LOOPBACK-only (127.0.0.1:9443); reachable via the SSH tunnel or the
#                 SSO gate below.
#   Netdata    -> real-time metrics dashboard (host + per-container + Redis). host net, :19999.
#   oauth2-proxy (optional, oauth2_enabled) -> a Keycloak SSO gate in front of the two
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
J_IMG="$(tfval jaeger_image "$ovl")";                J_IMG="${J_IMG:-jaegertracing/all-in-one:1.62}"
J_UI_PORT="$(tfval jaeger_ui_port "$ovl")";          J_UI_PORT="${J_UI_PORT:-16686}"
J_UI_BIND="$(tfval jaeger_ui_bind "$ovl")";          J_UI_BIND="${J_UI_BIND:-127.0.0.1}"
J_OTLP_HTTP="$(tfval jaeger_otlp_http_port "$ovl")"; J_OTLP_HTTP="${J_OTLP_HTTP:-4318}"
J_OTLP_GRPC="$(tfval jaeger_otlp_grpc_port "$ovl")"; J_OTLP_GRPC="${J_OTLP_GRPC:-4317}"
J_MEM="$(tfval jaeger_mem "$ovl")";                  J_MEM="${J_MEM:-300m}"
# sso gate
OA_EN="$(tfval oauth2_enabled "$ovl")";    OA_EN="${OA_EN:-false}"
OA_IMG="$(tfval oauth2_image "$ovl")";     OA_IMG="${OA_IMG:-quay.io/oauth2-proxy/oauth2-proxy:v7.6.0}"
ISSUER="$(tfval oauth2_issuer_url "$ovl")"
CLIENT_ID="$(tfval oauth2_client_id "$ovl")"; CLIENT_ID="${CLIENT_ID:-c360-oauth2-proxy}"
PUB_HOST="$(tfval oauth2_public_host "$ovl")"
P_PROXY="$(tfval portainer_proxy_port "$ovl")"; P_PROXY="${P_PROXY:-4443}"
N_PROXY="$(tfval netdata_proxy_port "$ovl")";   N_PROXY="${N_PROXY:-4199}"
# Per-dashboard SSO gating. Portainer has its OWN login AND a CSRF/origin check that rejects
# mutating requests behind a reverse proxy ("Forbidden - origin invalid"), so it is exposed
# DIRECTLY (portainer_sso=false). Netdata has no auth of its own, so keep it gated.
P_SSO="$(tfval portainer_sso "$ovl")"; P_SSO="${P_SSO:-true}"
N_SSO="$(tfval netdata_sso "$ovl")";   N_SSO="${N_SSO:-true}"
P_GATED=false; [[ "$OA_EN" == "true" && "$P_EN" == "true" && "$P_SSO" == "true" ]] && P_GATED=true
N_GATED=false; [[ "$OA_EN" == "true" && "$N_EN" == "true" && "$N_SSO" == "true" ]] && N_GATED=true
# Gated dashboards bind loopback (only the proxy reaches them); an un-gated but enabled
# dashboard binds all interfaces so the LB can reach it directly.
P_BIND="127.0.0.1"; [[ "$P_GATED" == "true" ]] || P_BIND="0.0.0.0"

[[ "$P_EN" == "true" || "$N_EN" == "true" || "$J_EN" == "true" ]] || { echo "ERROR: portainer_enabled, netdata_enabled and jaeger_enabled are all false in $ovl — nothing to do."; exit 1; }

# --- discover the target VM's floating IP from ../server outputs (by for_each key) ---
SERVERS_JSON="$( (cd ../server && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -json servers 2>/dev/null) || true )"
[[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no ../server servers output for $ENV — deploy the server first."; exit 1; }
FIP="$(printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get("floating_ip") for i in (s.get("internal_interfaces") or []) if i.get("floating_ip")), ""))' "$MON_SERVER_KEY")"
[[ -n "$FIP" ]] || { echo "ERROR: no floating IP for server key '$MON_SERVER_KEY' — define it in ../server/overlays/$ENV.tfvars."; exit 1; }
BASTION="${BASTION_USER:-leocdp360}@$FIP"
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)

if [[ "$ACTION" == "destroy" ]]; then
  echo ">> Removing monitoring containers on $BASTION ..."
  ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo docker rm -f c360-portainer c360-netdata c360-jaeger c360-oauth2-portainer c360-oauth2-netdata >/dev/null 2>&1; echo "   removed dashboards + Jaeger + SSO gate (data volumes kept)"'
  exit 0
fi

# --- optional Portainer admin bootstrap password ---
if [[ -n "${PORTAINER_ADMIN_PASSWORD:-}" && ${#PORTAINER_ADMIN_PASSWORD} -lt 12 ]]; then
  echo "ERROR: PORTAINER_ADMIN_PASSWORD must be >= 12 chars (Portainer rejects shorter)."; exit 1
fi
PADMIN_B64="$(printf %s "${PORTAINER_ADMIN_PASSWORD:-}" | base64 | tr -d '\n')"

# --- SSO gate: provision the Keycloak client + secrets locally (before we ship) ---
SEC_B64=""; COOKIE_B64=""; P_REDIRECT=""; N_REDIRECT=""
if [[ "$P_GATED" == "true" || "$N_GATED" == "true" ]]; then
  : "${ISSUER:?set oauth2_issuer_url in $ovl (e.g. http://<lb>:8080/realms/customer360)}"
  : "${PUB_HOST:?set oauth2_public_host in $ovl (the LB public IP/host the browser uses)}"
  KC_URL="${ISSUER%/realms/*}"; REALM="${ISSUER##*/realms/}"
  P_REDIRECT="http://$PUB_HOST:$P_PORT/oauth2/callback"
  N_REDIRECT="http://$PUB_HOST:$N_PORT/oauth2/callback"
  # only GATED dashboards get a callback URL registered on the Keycloak client
  REDIRECTS=""; [[ "$P_GATED" == "true" ]] && REDIRECTS="$P_REDIRECT"; [[ "$N_GATED" == "true" ]] && REDIRECTS="${REDIRECTS:+$REDIRECTS,}$N_REDIRECT"

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
[[ "$P_GATED" == "true" || "$N_GATED" == "true" ]] && echo "   SSO gate  : oauth2-proxy -> Keycloak ($ISSUER), client $CLIENT_ID"

# All params shipped in one base64 blob (dodges ssh arg-flattening; values are space-free).
PARAMS_B64="$(printf '%s\n' \
  "P_EN=$P_EN" "P_PORT=$P_PORT" "P_IMG=$P_IMG" "PADMIN_B64=$PADMIN_B64" "P_BIND=$P_BIND" \
  "P_GATED=$P_GATED" "N_GATED=$N_GATED" \
  "N_EN=$N_EN" "N_PORT=$N_PORT" "N_IMG=$N_IMG" \
  "J_EN=$J_EN" "J_IMG=$J_IMG" "J_UI_PORT=$J_UI_PORT" "J_UI_BIND=$J_UI_BIND" "J_OTLP_HTTP=$J_OTLP_HTTP" "J_OTLP_GRPC=$J_OTLP_GRPC" "J_MEM=$J_MEM" \
  "OA_EN=$OA_EN" "OA_IMG=$OA_IMG" "ISSUER=$ISSUER" "CLIENT_ID=$CLIENT_ID" \
  "SEC_B64=$SEC_B64" "COOKIE_B64=$COOKIE_B64" \
  "P_PROXY=$P_PROXY" "P_REDIRECT=$P_REDIRECT" "N_PROXY=$N_PROXY" "N_REDIRECT=$N_REDIRECT" \
  | base64 | tr -d '\n')"

ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$PARAMS_B64" <<'REMOTE'
set -euo pipefail
tmp="$(mktemp)"; printf %s "$1" | base64 -d > "$tmp"; set -a; . "$tmp"; set +a; rm -f "$tmp"
PADMIN="$(printf %s "$PADMIN_B64" | base64 -d 2>/dev/null || true)"
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
  sudo docker run -d --name c360-jaeger --restart unless-stopped --memory "${J_MEM:-300m}" -e COLLECTOR_OTLP_ENABLED=true -e SPAN_STORAGE_TYPE=badger -e BADGER_EPHEMERAL=false -e BADGER_DIRECTORY_VALUE=/badger/data -e BADGER_DIRECTORY_KEY=/badger/key -v jaeger_data:/badger -p "${J_UI_BIND:-127.0.0.1}:${J_UI_PORT:-16686}:16686" -p "0.0.0.0:${J_OTLP_HTTP:-4318}:4318" -p "0.0.0.0:${J_OTLP_GRPC:-4317}:4317" "$J_IMG"
  ok=0; for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:${J_UI_PORT:-16686}/" >/dev/null 2>&1 && { ok=1; break; }; sleep 2; done
  sudo docker ps --filter name=c360-jaeger --format '   running: {{.Names}} ({{.Status}})'
  [ "$ok" = "1" ] && echo "   Jaeger OK (UI :${J_UI_PORT:-16686}, OTLP http :${J_OTLP_HTTP:-4318} / grpc :${J_OTLP_GRPC:-4317})" || echo "   WARN: Jaeger not ready yet"
fi

# ---------- oauth2-proxy (Keycloak SSO gate, one per GATED dashboard) ----------
# Tear down any gate that should no longer exist (e.g. Portainer moved to direct access).
[ "$P_GATED" = "true" ] || sudo docker rm -f c360-oauth2-portainer >/dev/null 2>&1 || true
[ "$N_GATED" = "true" ] || sudo docker rm -f c360-oauth2-netdata   >/dev/null 2>&1 || true
if [ "$P_GATED" = "true" ] || [ "$N_GATED" = "true" ]; then
  sudo docker pull "$OA_IMG" >/dev/null || true
  run_proxy() {  # name listen redirect upstream extra_flag
    local name="$1" listen="$2" redirect="$3" upstream="$4" extra="$5"
    local args=(
      --provider=oidc --oidc-issuer-url="$ISSUER" --client-id="$CLIENT_ID" --client-secret="$SECRET"
      --cookie-secret="$COOKIE" --cookie-name="_oauth2_$name" --cookie-secure=false
      --email-domain="*" --insecure-oidc-allow-unverified-email=true
      --http-address="0.0.0.0:$listen" --redirect-url="$redirect" --upstream="$upstream"
      --reverse-proxy=true --skip-provider-button=true
    )
    [ -n "$extra" ] && args+=( "$extra" )
    sudo docker rm -f "c360-oauth2-$name" >/dev/null 2>&1 || true
    sudo docker run -d --name "c360-oauth2-$name" --restart unless-stopped --network host "$OA_IMG" "${args[@]}"
    local ok=0; for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:$listen/ping" >/dev/null 2>&1 && { ok=1; break; }; sleep 2; done
    sudo docker ps --filter "name=c360-oauth2-$name" --format '   running: {{.Names}} ({{.Status}})'
    [ "$ok" = "1" ] && echo "   oauth2-$name OK (/ping on :$listen)" || { echo "   WARN: oauth2-$name not ready — logs:"; sudo docker logs --tail 25 "c360-oauth2-$name" || true; }
  }
  if [ "$P_GATED" = "true" ]; then echo "   deploying oauth2-proxy for Portainer ..."; run_proxy portainer "$P_PROXY" "$P_REDIRECT" "https://127.0.0.1:$P_PORT" "--ssl-upstream-insecure-skip-verify=true"; fi
  if [ "$N_GATED" = "true" ]; then echo "   deploying oauth2-proxy for Netdata ...";   run_proxy netdata   "$N_PROXY" "$N_REDIRECT" "http://127.0.0.1:$N_PORT" ""; fi
fi
REMOTE

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
echo "   Admin tunnel (no LB): ssh -i $SSH_KEY -L $P_PORT:localhost:$P_PORT -L $N_PORT:localhost:$N_PORT $BASTION"
