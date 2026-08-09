# Composition module: wires the 4 managed services + in-DB bootstrap together.
# Instantiated by each environment (environments/<env>) with its own tfvars,
# backend and provider configuration. Nothing here configures providers - the
# environment does that and passes them in.

locals {
  name_prefix = "${var.name_prefix}-${var.environment}"
  repo_root   = "${path.module}/../.." # <repo>/terraform/stack -> <repo>

  effective_network_id = var.create_network ? module.network[0].network_id : var.network_id
  effective_subnet_id  = var.create_network ? module.network[0].subnet_id : var.subnet_id
}

# --- Networking (optional) -------------------------------------------------
module "network" {
  count  = var.create_network ? 1 : 0
  source = "../modules/network"

  create_network = true
  project_id     = var.vserver_project_id
  name_prefix    = local.name_prefix
  zone_id        = var.zone_id
  network_cidr   = var.network_cidr
  subnet_cidr    = var.subnet_cidr
}

# --- PostgreSQL ------------------------------------------------------------
module "postgres" {
  source = "../modules/postgres"

  topology            = var.pg_topology
  name_prefix         = local.name_prefix
  zone_id             = var.zone_id
  subnet_id           = local.effective_subnet_id
  engine_version      = var.pg_engine_version
  package_name        = var.pg_package_name
  volume_type         = var.pg_volume_type
  volume_size         = var.pg_volume_size
  cluster_nodes       = var.pg_cluster_nodes
  username            = var.pg_username
  password            = var.pg_password
  db_name             = var.pg_db_name
  public_access       = var.pg_public_access
  allowed_ip_prefixes = var.db_allowed_ip_prefixes
  config_values       = var.pg_config_values
}

# --- In-database bootstrap (extensions + RLS role + schema + seed) ----------
module "db_bootstrap" {
  source = "../modules/db-bootstrap"

  enabled         = var.run_db_bootstrap
  instance_id     = module.postgres.instance_id
  host            = var.pg_public_access ? module.postgres.public_host : module.postgres.host
  port            = module.postgres.port
  master_user     = var.pg_username
  master_password = var.pg_password
  db_name         = var.pg_db_name
  db_schema       = var.db_schema

  app_role_name     = var.app_role_name
  app_role_password = var.app_role_password

  extensions_sql = "${local.repo_root}/postgres/init/00-extensions.sql"
  schema_sql     = "${local.repo_root}/database-init/database-schema.sql"
  seed_sql       = "${local.repo_root}/database-init/init-core-database.sql"
  extra_sql      = var.db_bootstrap_extra_sql

  # Keycloak shares this Postgres server via a dedicated database (db_keycloak).
  # The app's KC_DB_URL (k8s c360-config) targets it, so provision it here too.
  create_keycloak_db = var.create_keycloak_db
  keycloak_db_sql    = "${local.repo_root}/postgres/init/02-create-keycloak-db.sql"
}

# --- Redis -----------------------------------------------------------------
module "redis" {
  source = "../modules/redis"

  name_prefix         = local.name_prefix
  zone_id             = var.zone_id
  subnet_id           = local.effective_subnet_id
  engine_version      = var.redis_engine_version
  package_name        = var.redis_package_name
  password            = var.redis_password
  public_access       = var.redis_public_access
  allowed_ip_prefixes = var.db_allowed_ip_prefixes
  config_values       = var.redis_config_values
}

# --- Kafka -----------------------------------------------------------------
module "kafka" {
  count  = var.kafka_enabled ? 1 : 0
  source = "../modules/kafka"

  name_prefix         = local.name_prefix
  project_id          = var.vserver_project_id
  network_id          = local.effective_network_id
  subnet_id           = local.effective_subnet_id
  kafka_version       = var.kafka_version
  package_name        = var.kafka_package_name
  volume_type         = var.kafka_volume_type
  storage_size        = var.kafka_storage_size
  broker_count        = var.kafka_broker_count
  sasl_authen         = var.kafka_sasl_authen
  mtls_authen         = var.kafka_mtls_authen
  public_access       = var.kafka_public_access
  allowed_ip_prefixes = var.db_allowed_ip_prefixes
  config_properties   = var.kafka_config_properties
  tenants             = var.tenants
  shared_topics       = var.kafka_shared_topics
  per_tenant_topics   = var.kafka_per_tenant_topics
}

# --- vStorage (S3-compatible buckets) --------------------------------------
module "vstorage" {
  count  = var.vstorage_enabled ? 1 : 0
  source = "../modules/vstorage"

  # aws provider is configured in the environment for the vStorage S3 endpoint.
  providers = {
    aws = aws
  }

  bucket_prefix      = var.vstorage_bucket_prefix
  tenants            = var.tenants
  shared_buckets     = var.vstorage_shared_buckets
  per_tenant_buckets = var.vstorage_per_tenant_buckets
  versioning         = var.vstorage_versioning
}
