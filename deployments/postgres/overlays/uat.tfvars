# UAT overlay — environment-specific, NON-SECRET config.
# Secrets (client_id/secret, db_password) live in ../terraform.tfvars or ../.env.
# Apply with:  ./deploy.sh uat <plan|apply>   (Terraform workspace "uat").

instance_name = "customer360-pg-uat"
db_name       = "customer360"
db_username   = "app_admin"

engine_version = "15"
package_name   = "db.s-general-2x4"
volume_type    = "ssd-iops200-HCM03-1C"
volume_size    = 20

# Network: create a fresh VPC + subnet for this env. Fill project_id below.
create_network = true
project_id     = "pro-8986f5c6-02ca-4647-be9a-4070bb100559"
network_name   = "c360-vpc-uat"
network_cidr   = "10.100.0.0/16"
subnet_name    = "c360-subnet-uat"
subnet_cidr    = "10.100.1.0/24"

zone_id           = "HCM03-1C" # only AZ enabled for this account (1A/1B: contact to enable); 3200 IOPS max standalone — see deployments/issues/
public_access     = false          # SECURE default. Use ./open-to-public.sh to open to your IP temporarily.
allowed_ip_prefix = ["10.0.0.0/8"] # VPC-internal only by default

backup_auto     = true
backup_time     = "00:00"
backup_duration = 7
