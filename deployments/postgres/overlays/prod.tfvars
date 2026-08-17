# PROD overlay — SAME config as UAT for now (bump sizing/backup when prod diverges).
# Secrets (client_id/secret, db_password) live in ../terraform.tfvars or ../.env.
# Apply with:  ./deploy.sh prod <plan|apply>   (Terraform workspace "prod").
#
# NOTE: instance_name is the one field intentionally differing from UAT so the two
# don't collide if they share a GreenNode project. In reality subnet_id (and later
# sizing) will also differ per env — change here when prod gets its own network.

instance_name = "leo-customer360-pg-prod"
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
