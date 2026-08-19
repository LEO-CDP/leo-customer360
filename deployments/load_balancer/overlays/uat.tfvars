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

# Expose both services. member_ip = each box's PRIVATE ip (see `terraform output servers`
# in ../server): api box 10.100.1.5, backend box 10.100.1.4.
backends = {
  "api" = {
    member_ip   = "10.100.1.5" # c360-api-uat-api (customer360-api)
    member_port = 8008
    listen_port = 80 # LB public :80 -> api:8008 (HTTPS needs Layer 7 + a cert)
    health_path = "/health"
  }
  "dagster" = {
    member_ip   = "10.100.1.4" # c360-api-uat-backend (backend-system / Dagster)
    member_port = 3000
    listen_port = 3000 # LB public :3000 -> dagster:3000
    health_path = null # TCP health check (no simple Dagster health path)
  }
  "keycloak" = {
    member_ip   = "10.100.1.5" # c360-api-uat-api (Keycloak co-located, see deployments/sso)
    member_port = 8080
    listen_port = 8080 # LB public :8080 -> keycloak:8080
    health_path = null # TCP health check: Keycloak's /health is on the mgmt port 9000, not 8080
  }
  "frontend" = {
    member_ip   = "10.100.1.5" # c360-api-uat-api (frontend-admin co-located, see deployments/frontend)
    member_port = 8890
    listen_port = 8890      # LB public :8890 -> frontend:8890
    health_path = "/health" # frontend-admin serves /health on 8890
  }
  "ads" = {
    member_ip   = "10.100.1.5" # c360-api-uat-api (ads-server co-located, see deployments/ads-server)
    member_port = 9009
    listen_port = 9009      # LB public :9009 -> ads:9009
    health_path = "/health" # ads-server serves /health on 9009
  }
  # Monitoring dashboards, fronted by oauth2-proxy (Keycloak SSO) — see deployments/monitoring.
  # The LB targets the PROXY ports (4443/4199), NOT the dashboards' own 9443/19999 (which stay
  # loopback/firewalled). /ping is oauth2-proxy's unauthenticated health endpoint.
  "portainer" = {
    member_ip   = "10.100.1.5" # c360-api-uat-api — Portainer DIRECT (its own login; not behind oauth2-proxy)
    member_port = 9443
    listen_port = 9443 # LB public https :9443 -> Portainer :9443 (L4 TLS passthrough, self-signed)
    health_path = null # Portainer is HTTPS on 9443 -> plain TCP health check (no HTTP /ping)
  }
  "netdata" = {
    member_ip   = "10.100.1.5" # c360-api-uat-api (oauth2-proxy -> Netdata 127.0.0.1:19999)
    member_port = 4199
    listen_port = 19999 # LB public :19999 -> oauth2-proxy:4199 -> Netdata
    health_path = "/ping"
  }
}

# Open the app ports on the backends' Default secgroup so the LB can reach them.
backend_security_group_id = "secg-7c1e85ec-8028-460a-8592-99463f198831" # Default secgroup on the boxes
backend_ingress_cidr      = "0.0.0.0/0"                                 # L4 NLB preserves client IP -> keep open
