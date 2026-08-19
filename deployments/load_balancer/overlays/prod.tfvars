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
