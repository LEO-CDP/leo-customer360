# UAT overlay — environment-specific, NON-SECRET config.
# Secrets (client_id/secret, db_password) live in ../terraform.tfvars or ../.env.
# Apply with:  ./deploy.sh uat <plan|apply>   (Terraform workspace "uat").

instance_name = "customer360-pg-uat"
db_name       = "customer360"
db_username   = "app_admin"

engine_version = "15"
package_name   = "db.s2-general-8x16"
volume_type    = "Gen2-NVMe2-IOPS10000" # HCM03-1C standalone max IOPS; 10000 needs HCM03-1A or cluster — see deployments/issues/
volume_size    = 250

# Network: create a fresh VPC + subnet for this env. Fill project_id below.
create_network = true
project_id     = "pro-8986f5c6-02ca-4647-be9a-4070bb100559"
network_name   = "c360-vpc-uat"
network_cidr   = "10.100.0.0/16"
subnet_name    = "c360-subnet-uat"
subnet_cidr    = "10.100.1.0/24"

zone_id           = "HCM03-1A" # enabled/default AZ for this account (1A/1B disabled); 3200 IOPS max standalone here — see deployments/issues/
public_access     = false
allowed_ip_prefix = ["10.0.0.0/8"]

backup_auto     = true
backup_time     = "00:00"
backup_duration = 7
