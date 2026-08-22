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
