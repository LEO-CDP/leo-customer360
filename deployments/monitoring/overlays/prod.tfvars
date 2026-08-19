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
