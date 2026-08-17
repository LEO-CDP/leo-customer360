# ---------------------------------------------------------------------------
# Network (optional): when create_network = true, create a VPC + subnet and
# attach the DB to it; otherwise use the existing var.subnet_id.
# ---------------------------------------------------------------------------
resource "vngcloud_vserver_network" "this" {
  count = var.create_network ? 1 : 0

  project_id = var.project_id
  name       = var.network_name
  cidr       = var.network_cidr
  zone_id    = var.zone_id
}

resource "vngcloud_vserver_subnet" "this" {
  count = var.create_network ? 1 : 0

  project_id = var.project_id
  name       = var.subnet_name
  network_id = vngcloud_vserver_network.this[0].id
  cidr       = var.subnet_cidr
  zone_id    = var.zone_id
}

locals {
  # Effective subnet: the one we just created, or the existing id.
  subnet_id = var.create_network ? vngcloud_vserver_subnet.this[0].id : var.subnet_id
}

# Resolve the compute package (CPU/RAM tier) shown in the console dropdown.
data "vngcloud_vdb_database_package" "pg" {
  engine_type    = "PostgreSQL"
  engine_version = var.engine_version
  name           = var.package_name
  zone_id        = var.zone_id
}

# Resolve the storage / volume tier.
data "vngcloud_vdb_database_volume_type" "pg" {
  type    = var.volume_type
  zone_id = var.zone_id
}

resource "vngcloud_vdb_relational_database" "pg" {
  name           = var.instance_name
  engine_type    = "PostgreSQL"
  engine_version = var.engine_version

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  package_id  = data.vngcloud_vdb_database_package.pg.id
  volume_type = data.vngcloud_vdb_database_volume_type.pg.id
  volume_size = var.volume_size

  subnet_id         = local.subnet_id
  zone_id           = var.zone_id
  public_access     = var.public_access
  allowed_ip_prefix = var.allowed_ip_prefix

  backup_auto     = var.backup_auto
  backup_time     = var.backup_time
  backup_duration = var.backup_duration

  action = "start"

  lifecycle {
    # The package/volume data sources return an EMPTY id on a no-match (rather
    # than erroring), which otherwise surfaces as a confusing "Missing required
    # argument" on package_id/volume_type. Fail early with an actionable message.
    precondition {
      condition     = try(data.vngcloud_vdb_database_package.pg.id, "") != ""
      error_message = "No vDB package matched package_name=\"${var.package_name}\" for PostgreSQL ${var.engine_version} in zone ${var.zone_id}. Copy the exact package name from the console create form."
    }
    precondition {
      condition     = try(data.vngcloud_vdb_database_volume_type.pg.id, "") != ""
      error_message = "No vDB volume type matched volume_type=\"${var.volume_type}\" in zone ${var.zone_id}. Copy the exact volume type from the console."
    }
    precondition {
      condition     = var.create_network || (var.subnet_id != "" && var.subnet_id != "sub-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
      error_message = "No subnet: set create_network=true to create a VPC+subnet, or provide a real subnet_id (sub-...)."
    }
    precondition {
      condition     = !var.create_network || (var.project_id != "" && var.project_id != "pro-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
      error_message = "project_id must be your REAL VNG Cloud project id (pro-...), not the placeholder. Find it in the console project selector / overview."
    }
  }
}
