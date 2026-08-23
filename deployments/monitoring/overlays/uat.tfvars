# UAT overlay — Portainer + Netdata run as Docker containers on the API box (co-located).
# Read by deploy-monitoring.sh (grep); no Terraform. No secrets here (an OPTIONAL
# Portainer admin password lives in .env, not in this file).

mon_server_key = "api"    # SHARE c360-api-uat-api (10.100.1.5): the box that runs
                          # api + ads + frontend + keycloak + redis.

# Portainer — container-ops UI (status/logs/exec/restart). HTTPS, bridge net.
portainer_enabled = true
portainer_port    = 9443              # NOT 9000 — Keycloak's mgmt/health port owns 9000.
portainer_image   = "portainer/portainer-ce:lts"

# Netdata — real-time metrics UI (host + per-container + Redis). host net, :19999.
netdata_enabled = true
netdata_port    = 19999               # Netdata's fixed default (see README to change it).
netdata_image   = "netdata/netdata:stable"

# --- oauth2-proxy: Keycloak SSO gate in front of BOTH dashboards ---
# The L4 NLB can't do OIDC, so oauth2-proxy enforces Keycloak login on the box:
#   LB :portainer_port -> proxy :portainer_proxy_port -> https://127.0.0.1:portainer_port
#   LB :netdata_port   -> proxy :netdata_proxy_port   -> http://127.0.0.1:netdata_port
# Read by deploy-monitoring.sh (grep). The client secret + cookie secret live in .env.
oauth2_enabled     = true
oauth2_image       = "quay.io/oauth2-proxy/oauth2-proxy:v7.6.0"
oauth2_public_host = "beta.leocdp.com"                              # public host the browser hits (Netdata still served on :19999)
oauth2_issuer_url  = "https://beta.leocdp.com/auth/realms/customer360" # KC via the LB (box hairpins; iss must match)
oauth2_client_id   = "c360-oauth2-proxy"                           # confidential client in the customer360 realm

# oauth2-proxy listen ports on the box = the LB backend member_port (kept off the in-use
# ports 6580/8008/8080/8890/9000/9009 and the dashboards' own 9443/19999).
portainer_proxy_port = 4443
netdata_proxy_port   = 4199

# Per-dashboard SSO gating. Portainer has its OWN login AND a CSRF/origin check that rejects
# mutating requests behind a reverse proxy ("Forbidden - origin invalid"), so expose it
# DIRECTLY (LB -> Portainer :9443, HTTPS/self-signed). Netdata has no auth -> keep it gated.
portainer_sso = false
netdata_sso   = true

# --- Jaeger (OpenTelemetry OTLP trace backend + UI) ---------------------------
# ON: SSO+TLS-gated at https://<domain>/jaeger (Caddy + oauth2-proxy). Kept always-on like
# Netdata so the /jaeger URL survives a deploy-monitoring re-run. Costs ~250-350 MB on the
# shared 1 vCPU / 2 GB api box (Jaeger badger + oauth2-jaeger) — set false to make it
# on-demand again (README.md, Jaeger section). App span emission is still gated separately
# per service by OTEL_SDK_DISABLED. badger = on-disk storage (low RAM).
jaeger_enabled = true
jaeger_image          = "jaegertracing/all-in-one:1.62.0"
jaeger_ui_port        = 16686
jaeger_ui_bind        = "127.0.0.1"   # loopback only; view via `ssh -L 16686:localhost:16686`
jaeger_otlp_http_port = 4318
jaeger_otlp_grpc_port = 4317
jaeger_mem            = "300m"         # docker --memory cap (protect the shared box)
# Gate the Jaeger UI behind oauth2-proxy / Keycloak (Jaeger has no native auth). Served over
# TLS at https://<domain>/jaeger via Caddy (deployments/proxy) -> oauth2-proxy on
# :jaeger_proxy_port -> Jaeger. (No dedicated LB port; the old :16686 listener was retired.)
jaeger_sso        = true
jaeger_proxy_port = 4686   # oauth2-proxy listen port on the box (LB backend member_port)

# --- pgAdmin (web Postgres admin/monitoring UI) ------------------------------
# Occasional-use DB admin tool, exposed DIRECTLY on the LB with its OWN login (pgadmin_sso =
# false), like Portainer: public LB :5050 -> pgAdmin :5050, pgAdmin authenticates. No Keycloak
# gate (single login). CAVEAT: unlike Portainer (self-signed HTTPS), pgAdmin serves plain HTTP
# and the L4 LB has no TLS, so the DB-admin login page is public + credentials cross the wire in
# cleartext — accepted here as a deliberate uat tradeoff for a single login. It's the HEAVIEST
# of the four tools (~150-250 MB) on the shared 1 vCPU / 2 GB api box, so it's capped and easy to
# turn off: set pgadmin_enabled = false to run it on-demand (like Jaeger) if the box gets tight.
# (To harden later: gate it (pgadmin_sso = true) or front it with Caddy TLS like Jaeger.)
# Admin tunnel: ssh -i ~/.ssh/c360-api_ed25519 -L 5050:localhost:5050 leocdp360@<api-box-fip>
# Login password lives ONLY in .env (PGADMIN_DEFAULT_PASSWORD) — auto-generated on first deploy.
pgadmin_enabled = true
pgadmin_port    = 5050                        # dodges in-use 6580/8008/8080/8890/9000/9009/9443/16686/19999
pgadmin_image   = "dpage/pgadmin4:8.14"
pgadmin_bind    = "0.0.0.0"                    # exposed to the LB directly (own login; cleartext caveat above)
pgadmin_email   = "admin@leocdp.com"          # first-login user (password in .env)
pgadmin_mem     = "384m"                       # docker --memory cap (protect the shared box)
pgadmin_sso       = false                       # DIRECT, own login (no Keycloak gate)
pgadmin_proxy_port = 4050                       # (unused while pgadmin_sso = false; kept for a quick re-gate)

# --- Portainer agents on OTHER boxes (one Portainer manages every box) --------
# Portainer runs on the api box and by default only sees the api box's containers. To manage the
# BACKEND box (server key "1x2" = 10.100.1.4, runs backend-system/Dagster) from the SAME Portainer,
# run portainer/agent there and register it as an environment (no second Portainer). Comma-separate
# for more boxes. Portainer reaches the agent over the private VPC at <box-private-ip>:9001.
# PREREQUISITE (infra, one-time): open tcp/9001 on the Default secgroup from the api box's private
# IP — set in ../server/overlays/uat.tfvars (agent_ports) and apply with
# `cd ../server && ./deploy.sh uat apply`. deploy-monitoring auto-registers the env via the
# Portainer API (needs PORTAINER_ADMIN_PASSWORD in .env; else it prints the one-click UI step).
portainer_agent_server_keys = "1x2"
portainer_agent_image       = "portainer/agent:lts"
portainer_agent_port        = 9001
