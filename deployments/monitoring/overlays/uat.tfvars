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
