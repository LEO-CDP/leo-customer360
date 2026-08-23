# UAT overlay — environment-specific, NON-SECRET config.
# Secrets (client_id/secret) live in ../terraform.tfvars or ../.env.
# Apply with:  ./deploy.sh uat <plan|apply>   (Terraform workspace "uat").

lb_name      = "customer360-nlb-uat"
package_name = "NLB_Small"
# HCM03-1C NLB_Small uuid (from ?zoneId=HCM03-1C); the data source returns the default-AZ
# one which the create API rejects. Bypass it with this direct id.
package_id = "lbp-f60d5354-0600-11f0-a0a4-ec2a72332f83"
lb_type    = "Layer 4" # Layer 4 = Network Load Balancer (NLB)
scheme     = "Internet"

# Project the LB is created in (same account/project as the vDB postgres deploy).
project_id = "pro-8986f5c6-02ca-4647-be9a-4070bb100559"

# Network: reuse the EXISTING subnet the backends live in (do NOT create an
# isolated VPC — it couldn't route to them). After `deployments/postgres`
# applies its UAT VPC (c360-vpc-uat / c360-subnet-uat), copy that subnet id here.
create_network = false
subnet_id      = "sub-7c1f6eff-7244-4a29-a3cf-3592745ea0e7" # same subnet as the backends (server/postgres, HCM03-1C)

zone_id = "HCM03-1C" # only AZ enabled for this account

# After the beta.leocdp.com cutover the app is fronted by Caddy (deployments/proxy):
# the LB does dumb :80/:443 TCP passthrough -> Caddy. Ops tools stay on their own ports.
# member_ip = each box PRIVATE ip (see `terraform output servers`): api 10.100.1.5, backend 10.100.1.4.
backends = {
  # HTTPS front door: Caddy (deployments/proxy) terminates TLS + path-routes
  #   beta.leocdp.com/ -> frontend · /c360api -> api · /auth -> keycloak · /ads -> ads
  # The L4 NLB just passes TCP through; health = plain TCP connect.
  "caddy_https" = {
    member_ip   = "10.100.1.5" # c360-api-uat-api (Caddy, --network host)
    member_port = 443
    listen_port = 443
    health_path = null # TLS on 443 -> TCP health check
  }
  "caddy_http" = {
    member_ip   = "10.100.1.5" # Caddy :80 = ACME HTTP-01 challenge + HTTP->HTTPS redirect
    member_port = 80
    listen_port = 80
    health_path = null # :80 redirects (no 200 body) -> TCP health check
  }
  # --- ops tools stay raw on their own ports (they don't sub-path cleanly; see proxy/README) ---
  "dagster" = {
    member_ip   = "10.100.1.4" # c360-api-uat-backend (backend-system / Dagster)
    member_port = 3000
    listen_port = 3000
    health_path = null
  }
  "portainer" = {
    member_ip   = "10.100.1.5" # Portainer DIRECT (own login, self-signed TLS)
    member_port = 9443
    listen_port = 9443
    health_path = null
  }
  "netdata" = {
    member_ip   = "10.100.1.5" # oauth2-proxy -> Netdata (Keycloak SSO)
    member_port = 4199
    listen_port = 19999
    health_path = "/ping"
  }
  # pgAdmin exposed DIRECTLY with its own login (deployments/monitoring pgadmin_sso = false,
  # pgadmin_bind = 0.0.0.0): LB :5050 -> pgAdmin :5050. Plain HTTP (no TLS) — cleartext login,
  # accepted uat tradeoff (see the monitoring overlay). Health-check pgAdmin's own /misc/ping.
  "pgadmin" = {
    member_ip   = "10.100.1.5" # c360-api-uat-api — pgAdmin direct (its own login)
    member_port = 5050
    listen_port = 5050
    health_path = "/misc/ping"
  }
}
# Jaeger is fronted by Caddy at https://beta.leocdp.com/jaeger (see deployments/proxy) — no dedicated LB listener.

# Open the app ports on the backends' Default secgroup so the LB can reach them.
backend_security_group_id = "secg-7c1e85ec-8028-460a-8592-99463f198831" # Default secgroup on the boxes
backend_ingress_cidr      = "0.0.0.0/0"                                 # L4 NLB preserves client IP -> keep open
