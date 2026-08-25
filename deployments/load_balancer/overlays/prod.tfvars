# PROD overlay — SAME config as UAT for now (bump the package tier when prod
# traffic diverges, e.g. NLB_Medium / NLB_Large).
# Secrets (client_id/secret) live in ../terraform.tfvars or ../.env.
# Apply with:  ./deploy.sh prod <plan|apply>   (Terraform workspace "prod").
#
# NOTE: lb_name differs from UAT so the two don't collide. subnet_id also
# differs per env — set prod's real subnet before apply.

lb_name      = "customer360-nlb-prod"
package_name = "NLB_Small"
lb_type      = "Layer 4" # Layer 4 = Network Load Balancer (NLB)
scheme       = "Internet"

# Project the LB is created in (same account/project as the vDB postgres deploy).
project_id = "pro-8986f5c6-02ca-4647-be9a-4070bb100559" # same account/project for now

# Network: reuse the EXISTING subnet the backends live in (do NOT create an
# isolated VPC — it couldn't route to them). After `deployments/postgres`
# applies its PROD VPC (c360-vpc-prod / c360-subnet-prod), copy that subnet id here.
create_network = false
subnet_id      = "sub-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" # <-- set the real sub-... id

zone_id = "HCM03-1C" # enabled/default AZ for this account (1A/1B disabled)

# --- Backends exposed through the LB ---
# PROD splits app services onto dedicated vServers (server keys ads/sso/frontend); the
# monitoring stack stays on the api box (mon_server_key="api"). Only the monitoring
# dashboards are wired here for now — fronted by oauth2-proxy/Keycloak (see
# deployments/monitoring). The LB targets the PROXY ports (4443/4199), NOT the dashboards'
# own 9443/19999 (which stay loopback/firewalled). /ping is oauth2-proxy's unauthenticated
# health endpoint. Add api/keycloak/frontend/ads backends here as those prod services come
# online (mirror overlays/uat.tfvars, each pointing at its dedicated box's private ip).
backends = {
  "portainer" = {
    member_ip   = "REPLACE_WITH_PROD_API_IP" # PROD api box private ip — Portainer DIRECT (its own login)
    member_port = 9443
    listen_port = 9443 # LB public https :9443 -> Portainer :9443 (L4 TLS passthrough, self-signed)
    health_path = null # Portainer is HTTPS on 9443 -> plain TCP health check (no HTTP /ping)
  }
  "netdata" = {
    member_ip   = "REPLACE_WITH_PROD_API_IP" # PROD api box private ip (oauth2-proxy -> Netdata 127.0.0.1:19999)
    member_port = 4199
    listen_port = 19999 # LB public :19999 -> oauth2-proxy:4199 -> Netdata
    health_path = "/ping"
  }
  # pgAdmin behind Keycloak SSO (deployments/monitoring pgadmin_sso = true, pgadmin_bind = 127.0.0.1):
  # LB :5050 -> oauth2-proxy :4050 on the box -> Keycloak -> pgAdmin 127.0.0.1:5050. Health-check the
  # PROXY's /ping (not pgAdmin's /misc/ping), same as the netdata backend above.
  "pgadmin" = {
    member_ip   = "REPLACE_WITH_PROD_API_IP" # oauth2-proxy :4050 -> pgAdmin 127.0.0.1:5050 (Keycloak SSO)
    member_port = 4050
    listen_port = 5050
    health_path = "/ping"
  }
  # redis-commander (broker Redis viewer) on the prod tracking box — own basic-auth login (direct).
  # Uncomment once the "tracking" server key is provisioned and fill its private ip.
  # "redis-commander" = {
  #   member_ip   = "REPLACE_WITH_PROD_TRACKING_IP"
  #   member_port = 8081
  #   listen_port = 8081
  #   health_path = null # basic auth returns 401 on / -> plain TCP health check
  # }
}
# Jaeger is fronted by Caddy at https://<domain>/jaeger (see deployments/proxy) — no dedicated LB listener.

# Open the proxy ports (4443/4199) on the backends' security group so the LB can reach them.
backend_security_group_id = "secg-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" # <-- PROD boxes' security group
backend_ingress_cidr      = "0.0.0.0/0"                                 # L4 NLB preserves client IP
