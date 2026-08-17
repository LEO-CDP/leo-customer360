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

  subnet_id         = var.subnet_id
  zone_id           = var.zone_id
  public_access     = var.public_access
  allowed_ip_prefix = var.allowed_ip_prefix

  backup_auto     = var.backup_auto
  backup_time     = var.backup_time
  backup_duration = var.backup_duration

  action = "start"
}
