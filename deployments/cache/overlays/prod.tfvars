# PROD overlay — managed VNG MemStore (Redis), package db.s-general-2x4.
# Apply with:  ./deploy.sh prod apply     (runs Terraform, workspace "prod")
#
# Secrets (client_id/secret, redis_password) live in terraform.tfvars / .env.
# NOTE: prod compute/DB are not deployed yet — fill subnet_id (and confirm the
# engine_version/package exist in HCM03-1C) before applying.

deploy_managed = true

instance_name  = "c360-redis-prod"
engine_version = "7.0"               # VERIFY an available Redis version in the console MemStore form
package_name   = "db.s-general-2x4"  # 2 vCPU / 4 GB
zone_id        = "HCM03-1C"

# The SAME subnet as the prod api server (so it is reachable privately). vDB public
# access is non-functional on this platform, so this MUST be an in-VPC subnet.
subnet_id = "sub-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" # TODO: set to the prod subnet id

public_access     = false
allowed_ip_prefix = ["10.100.0.0/16"] # VPC only

backup_auto     = true
backup_duration = 2
backup_time     = "00:00"
