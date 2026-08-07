terraform {
  required_providers {
    vngcloud = {
      source = "vngcloud/vngcloud"
    }
  }
}

# Managed Redis on vDB Memory store (single shared instance for all tenants).
#
# The Customer360 app uses Redis only as a fail-open cache (HTTP responses +
# Keycloak token/identity). Tenant isolation lives in Postgres RLS, not Redis,
# so one shared instance is the correct match for the code today.
#
# NOTE on engine_version: managed vDB Redis is 7.2.x (the repo's docker image is
# Redis 8, but the app only uses GET/SET/SCAN/DEL/TTL so 7.2 is functionally
# equivalent). Confirm the exact accepted version string in the vDB console /
# via `terraform plan` (the database_package data source fails if it can't match
# engine_type + engine_version + name).

data "vngcloud_vdb_database_package" "redis" {
  engine_type    = "Redis"
  engine_version = var.engine_version
  name           = var.package_name
  zone_id        = var.zone_id
}

# maxmemory-policy / appendonly etc. are set here (managed Redis has no mounted
# redis.conf). Leave config_values empty to skip the config group entirely.
resource "vngcloud_vdb_memstore_config_group" "this" {
  count = length(var.config_values) > 0 ? 1 : 0

  engine_type    = "Redis"
  engine_version = var.engine_version
  name           = "${var.name_prefix}-redis-cfg"
  values         = var.config_values
}

resource "vngcloud_vdb_memstore_database" "this" {
  name           = "${var.name_prefix}-redis"
  engine_type    = "Redis"
  engine_version = var.engine_version
  subnet_id      = var.subnet_id
  zone_id        = var.zone_id
  package_id     = data.vngcloud_vdb_database_package.redis.id
  public_access  = var.public_access

  redis_password         = var.password
  redis_password_enabled = true
  allowed_ip_prefix      = var.allowed_ip_prefixes

  config_id = length(var.config_values) > 0 ? vngcloud_vdb_memstore_config_group.this[0].id : null

  action          = "start"
  backup_auto     = var.backup_auto
  backup_duration = var.backup_duration
  backup_time     = var.backup_time
}
