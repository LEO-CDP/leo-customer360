# PROD overlay — SAME shape as UAT for now (bump disk/network when prod diverges).
# Secrets (client_id/secret, user_password) live in ../terraform.tfvars or ../.env.
# Apply with:  ./deploy.sh prod <plan|apply>   (Terraform workspace "prod").
#
# NOTE: name_prefix + subnet CIDRs differ from UAT so the two envs don't collide.

name_prefix = "c360-api-prod"

# Both API-server sizes from the request, side by side in this env.
servers = {
  "4x8" = {
    flavor_name    = "s2-general-4x8" # 4 vCPU / 8 GB
    root_disk_size = 50
  }
  "8x16" = {
    flavor_name    = "s2-general-8x16" # 8 vCPU / 16 GB
    root_disk_size = 50
  }
}

# Catalog names — VERIFY against the console create form for this account/zone.
flavor_zone_name      = "General v2 Instances"
volume_type_zone_name = "SSD"
image_name            = "Ubuntu 24.04 x64"
root_disk_type_name   = "SSD-IOPS3000"

# Network: create a fresh VPC + subnet for this env. Fill project_id below.
create_network = true
project_id     = "pro-8986f5c6-02ca-4647-be9a-4070bb100559" # same account/project for now
network_name   = "c360-api-vpc-prod"
network_cidr   = "10.101.0.0/16"
subnet_name    = "c360-api-subnet-prod"
subnet_cidr    = "10.101.1.0/24"

zone_id           = "HCM03-1A" # enabled/default AZ for this account (1A/1B disabled) — see deployments/issues/
encryption_volume = false

# Login: register an SSH key and attach it to both servers (recommended).
# Set ssh_public_key in ../terraform.tfvars or ../.env (TF_VAR_ssh_public_key).
create_ssh_key = true
ssh_key_name   = "c360-api-prod"

# Admin user for password login; password set in ../terraform.tfvars (user_password).
user_name = "leocdp360"

# Existing security groups to attach (secg-...). Empty = provider default.
security_group = []
