# PROD overlay — monitoring UIs. Defaults to the api box, same as UAT. If you'd rather
# ISOLATE monitoring from the workload (recommended once prod has load), point
# mon_server_key at a dedicated server key defined in ../server/overlays/prod.tfvars.

mon_server_key = "api"    # -> set to a dedicated box (e.g. "mon") to isolate monitoring.

portainer_enabled = true
portainer_port    = 9443
portainer_image   = "portainer/portainer-ce:lts"

netdata_enabled = true
netdata_port    = 19999
netdata_image   = "netdata/netdata:stable"
# Monitor Redis via Netdata's built-in go.d/redis collector (no extra container). Needs the Redis
# password from ../cache (TF_VAR_redis_password or ../cache/terraform.tfvars). Set false to disable.
netdata_redis_monitor = true
netdata_redis_port    = 6580

# --- oauth2-proxy: Keycloak SSO gate (see uat.tfvars for the flow) ---
# FILL IN for prod: oauth2_public_host + oauth2_issuer_url must be the PROD LB /
# Keycloak public URL (prod runs Keycloak on its own 'sso' box behind the LB).
oauth2_enabled     = true
oauth2_image       = "quay.io/oauth2-proxy/oauth2-proxy:v7.6.0"
oauth2_public_host = "REPLACE_WITH_PROD_LB_IP"
oauth2_issuer_url  = "http://REPLACE_WITH_PROD_LB_IP:8080/realms/customer360"
oauth2_client_id   = "c360-oauth2-proxy"
portainer_proxy_port = 4443
netdata_proxy_port   = 4199

# Portainer exposed DIRECTLY (its own login + reverse-proxy CSRF issue); Netdata gated.
portainer_sso = false
netdata_sso   = true

# --- Jaeger (OpenTelemetry OTLP trace backend + UI) ---------------------------
# ON in prod (dedicated boxes have headroom); apps export at 10% head sampling.
# Runs on the monitoring box (mon_server_key). OTLP ports are published on 0.0.0.0
# so each per-service box reaches them over the private VPC; the UI stays on
# loopback — reach it via the admin tunnel, or gate it behind oauth2-proxy like
# the other dashboards. Give it more RAM here than on the shared uat box.
jaeger_enabled        = true
jaeger_image          = "jaegertracing/all-in-one:1.62.0"
jaeger_ui_port        = 16686
jaeger_ui_bind        = "127.0.0.1"
jaeger_otlp_http_port = 4318
jaeger_otlp_grpc_port = 4317
jaeger_mem            = "1g"
# Gate the Jaeger UI behind oauth2-proxy / Keycloak and expose via the LB on :16686
# (Jaeger has no native auth). LB 'jaeger' backend maps public :16686 -> :jaeger_proxy_port.
jaeger_sso        = true
jaeger_proxy_port = 4686

# --- pgAdmin (web Postgres admin/monitoring UI) ------------------------------
# GATED behind Keycloak SSO (pgadmin_sso = true), same model as Netdata/Jaeger: public LB :5050
# -> oauth2-proxy :4050 on the box -> Keycloak login -> pgAdmin (loopback :5050), then pgAdmin's
# OWN login. pgAdmin serves plain HTTP with no TLS of its own, so it must NOT sit raw on the LB —
# the SSO gate is how it's reached safely. Requires oauth2_enabled = true (above) and the matching
# 'pgadmin' backend in ../load_balancer/overlays/prod.tfvars (member_port = 4050). Login password
# lives ONLY in .env (PGADMIN_DEFAULT_PASSWORD), auto-generated on first deploy. Runs on mon_server_key.
pgadmin_enabled = true
pgadmin_port    = 5050
pgadmin_image   = "dpage/pgadmin4:8.14"
pgadmin_bind    = "127.0.0.1"                 # loopback: only the oauth2-proxy (SSO gate) reaches it
pgadmin_email   = "admin@leocdp.com"
pgadmin_mem     = "512m"                        # more headroom than uat (dedicated/bigger box)
pgadmin_sso       = true                         # GATE the UI behind Keycloak SSO (oauth2-proxy)
pgadmin_proxy_port = 4050                        # oauth2-proxy listen port on the box (LB 'pgadmin' backend member_port)

# --- Portainer agents on OTHER boxes (one Portainer manages every box) --------
# Set to the comma-separated ../server keys of the boxes whose containers Portainer should manage
# (besides mon_server_key). Empty by default here since the prod topology may differ from uat —
# fill in the prod backend/worker keys. PREREQUISITE (infra, one-time): open tcp/9001 on those
# boxes' secgroup from the Portainer box's private IP (../server agent_ports + ./deploy.sh prod apply).
portainer_agent_server_keys = ""
portainer_agent_image       = "portainer/agent:lts"
portainer_agent_port        = 9001
