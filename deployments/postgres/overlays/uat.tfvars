# UAT overlay — environment-specific, NON-SECRET config.
# Secrets (client_id/secret, db_password) live in ../terraform.tfvars or ../.env.
# Apply with:  ./deploy.sh uat <plan|apply>   (Terraform workspace "uat").

instance_name = "leo-customer360-pg-uat"
db_name       = "customer360"
db_username   = "app_admin"

engine_version = "16"
package_name   = "db.s-general-8x16"
volume_type    = "Gen2-NVMe2-IOPS10000"
volume_size    = 250

subnet_id         = "sub-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
zone_id           = "HCM03-1A"
public_access     = false
allowed_ip_prefix = ["10.0.0.0/8"]

backup_auto     = true
backup_time     = "00:00"
backup_duration = 7
