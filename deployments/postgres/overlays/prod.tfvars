# PROD overlay — SAME config as UAT for now (bump sizing/backup when prod diverges).
# Secrets (client_id/secret, db_password) live in ../terraform.tfvars or ../.env.
# Apply with:  ./deploy.sh prod <plan|apply>   (Terraform workspace "prod").
#
# NOTE: instance_name (vDB limit: 6-20 chars) differs from UAT so the two don't
# collide. subnet_id will also differ per env — set prod's real subnet before apply.

instance_name = "customer360-pg-prod"
db_name       = "customer360"
db_username   = "app_admin"

engine_version = "15"
package_name   = "db.s-general-8x16"
volume_type    = "ssd-iops3200-HCM03-1C" # HCM03-1C standalone max IOPS; 10000 needs HCM03-1A or cluster — see deployments/issues/
volume_size    = 250

# Network: create a fresh VPC + subnet for this env. Fill project_id below.
create_network = true
project_id     = "pro-8986f5c6-02ca-4647-be9a-4070bb100559" # same account/project for now
network_name   = "c360-vpc-prod"
network_cidr   = "10.101.0.0/16"
subnet_name    = "c360-subnet-prod"
subnet_cidr    = "10.101.1.0/24"

zone_id           = "HCM03-1C" # enabled/default AZ for this account (1A/1B disabled); 3200 IOPS max standalone here — see deployments/issues/
public_access     = false
allowed_ip_prefix = ["10.0.0.0/8"]

backup_auto     = true
backup_time     = "00:00"
backup_duration = 7
