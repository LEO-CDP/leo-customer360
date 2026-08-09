terraform {
  required_providers {
    vngcloud = {
      source = "vngcloud/vngcloud"
    }
  }
}

# Managed PostgreSQL on vDB, one shared instance for all tenants.
#
# Tenant isolation is done inside the DB via Row-Level Security on tenant_id
# (see database-init/database-schema.sql), so a single shared cluster is the
# correct topology. Per-environment we switch between:
#   * topology = "standalone" -> vngcloud_vdb_relational_database (single node, cheaper, dev/PoC)
#   * topology = "cluster"    -> vngcloud_vdb_postgresql_cluster  (2-10 nodes HA, RW+RO ports, prod)
#
# The extensions the app actually needs (uuid-ossp, pgcrypto, vector/pgvector,
# postgis, pg_trgm, fuzzystrmatch - see postgres/init/00-extensions.sql) are all
# supported by vDB PostgreSQL, but the provider does NOT manage in-database
# objects. Extensions, the customer360_app RLS role, schema, seed and the
# dedicated db_keycloak database are applied by the db-bootstrap module (or your
# CI/K8s migration job).

locals {
  is_cluster = var.topology == "cluster"
}

# --------------------------------------------------------------------------
# Standalone (single node)
# --------------------------------------------------------------------------
data "vngcloud_vdb_database_package" "standalone" {
  count          = local.is_cluster ? 0 : 1
  engine_type    = "PostgreSQL"
  engine_version = var.engine_version
  name           = var.package_name
  zone_id        = var.zone_id
}

data "vngcloud_vdb_database_volume_type" "standalone" {
  count   = local.is_cluster ? 0 : 1
  type    = var.volume_type
  zone_id = var.zone_id
}

resource "vngcloud_vdb_relational_config_group" "standalone" {
  count          = (!local.is_cluster && length(var.config_values) > 0) ? 1 : 0
  engine_type    = "PostgreSQL"
  engine_version = var.engine_version
  name           = "${var.name_prefix}-pg-cfg"
  values         = var.config_values
}

resource "vngcloud_vdb_relational_database" "standalone" {
  count = local.is_cluster ? 0 : 1

  name           = "${var.name_prefix}-pg"
  engine_type    = "PostgreSQL"
  engine_version = var.engine_version
  subnet_id      = var.subnet_id
  zone_id        = var.zone_id
  package_id     = data.vngcloud_vdb_database_package.standalone[0].id
  volume_type    = data.vngcloud_vdb_database_volume_type.standalone[0].id
  volume_size    = var.volume_size

  username      = var.username
  password      = var.password
  db_name       = var.db_name
  public_access = var.public_access

  allowed_ip_prefix = var.allowed_ip_prefixes

  action          = "start"
  backup_auto     = var.backup_auto
  backup_duration = var.backup_duration
  backup_time     = var.backup_time

  config_id = (!local.is_cluster && length(var.config_values) > 0) ? vngcloud_vdb_relational_config_group.standalone[0].id : null
}

# --------------------------------------------------------------------------
# Cluster (HA, 2-10 nodes)
# --------------------------------------------------------------------------
data "vngcloud_vdb_postgresql_cluster_package" "cluster" {
  count   = local.is_cluster ? 1 : 0
  name    = var.package_name
  zone_id = var.zone_id
}

data "vngcloud_vdb_postgresql_cluster_volume_type" "cluster" {
  count   = local.is_cluster ? 1 : 0
  type    = var.volume_type
  zone_id = var.zone_id
}

resource "vngcloud_vdb_postgresql_cluster_config_group" "cluster" {
  count          = (local.is_cluster && length(var.config_values) > 0) ? 1 : 0
  engine_version = var.engine_version
  name           = "${var.name_prefix}-pg-cluster-cfg"
  values         = var.config_values
}

resource "vngcloud_vdb_postgresql_cluster" "cluster" {
  count = local.is_cluster ? 1 : 0

  name            = "${var.name_prefix}-pg"
  engine_version  = var.engine_version
  subnet_id       = var.subnet_id
  zone_id         = var.zone_id
  package_id      = data.vngcloud_vdb_postgresql_cluster_package.cluster[0].id
  volume_type_id  = data.vngcloud_vdb_postgresql_cluster_volume_type.cluster[0].id
  volume_size     = var.volume_size
  number_of_nodes = var.cluster_nodes
  public_access   = var.public_access

  username = var.username
  password = var.password
  db_name  = var.db_name

  config_id = (local.is_cluster && length(var.config_values) > 0) ? vngcloud_vdb_postgresql_cluster_config_group.cluster[0].id : null

  # Read-write listener on 5432 for every allowed CIDR. Add 15432 rules for the
  # read-only endpoint if your workers use it.
  dynamic "secgroup_rule" {
    for_each = var.allowed_ip_prefixes
    content {
      remote_ip_prefix = secgroup_rule.value
      port             = 5432
    }
  }
}
