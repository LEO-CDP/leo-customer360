# ---------------------------------------------------------------------------
# PROD path only: a managed VNG MemStore (Redis) instance.
#
# UAT does NOT use this file — deploy.sh uat runs a Docker container on the api
# box over SSH and never invokes Terraform. The resource is count-gated on
# var.deploy_managed so applying with overlays/uat.tfvars (deploy_managed=false)
# is a clean no-op.
#
# NOTE (unverified): engine_type="Redis" for the package lookup and the exact
# available engine_version are from the provider docs / console — confirm them
# against the MemStore create form. A wrong name/version yields an EMPTY package
# id, which the precondition below turns into an actionable error.
# ---------------------------------------------------------------------------

# Resolve the MemStore package (CPU/RAM tier, e.g. db.s-general-2x4) to its id.
data "vngcloud_vdb_database_package" "redis" {
  count = var.deploy_managed ? 1 : 0

  engine_type    = "Redis"
  engine_version = var.engine_version
  name           = var.package_name
  zone_id        = var.zone_id
}

resource "vngcloud_vdb_memstore_database" "this" {
  count = var.deploy_managed ? 1 : 0

  name           = var.instance_name
  engine_type    = "Redis"
  engine_version = var.engine_version

  subnet_id  = var.subnet_id
  zone_id    = var.zone_id
  package_id = data.vngcloud_vdb_database_package.redis[0].id

  redis_password_enabled = true
  redis_password         = var.redis_password

  public_access     = var.public_access
  allowed_ip_prefix = var.allowed_ip_prefix

  backup_auto     = var.backup_auto
  backup_duration = var.backup_duration
  backup_time     = var.backup_time

  action = "start"

  lifecycle {
    precondition {
      condition     = try(data.vngcloud_vdb_database_package.redis[0].id, "") != ""
      error_message = "No vDB package matched package_name=\"${var.package_name}\" for Redis ${var.engine_version} in zone ${var.zone_id}. Copy the exact package name/version/engine from the console MemStore create form."
    }
    precondition {
      condition     = var.subnet_id != "" && var.subnet_id != "sub-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
      error_message = "subnet_id is required for the managed MemStore — set it to the SAME subnet as the prod api server (sub-...)."
    }
  }
}
