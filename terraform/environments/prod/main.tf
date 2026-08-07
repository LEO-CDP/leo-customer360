# PROD environment: HA PostgreSQL cluster, shared Redis, shared Kafka cluster
# with BOTH shared topics (tenant_id in key) and per-tenant topics + scoped SASL
# users, and versioned vStorage buckets (shared + per-tenant). Hybrid tenancy.

module "stack" {
  source = "../../stack"

  # When a module receives an explicit providers map, default inheritance is
  # disabled, so every provider the stack uses must be listed here.
  providers = {
    vngcloud = vngcloud
    aws      = aws
    null     = null
  }

  environment = "prod"
  name_prefix = var.name_prefix

  zone_id            = var.zone_id
  vserver_project_id = var.vserver_project_id

  # --- Networking: reference existing prod VPC; restricted CIDRs ---
  create_network         = var.create_network
  network_id             = var.network_id
  subnet_id              = var.subnet_id
  network_cidr           = var.network_cidr
  subnet_cidr            = var.subnet_cidr
  db_allowed_ip_prefixes = var.db_allowed_ip_prefixes

  tenants = var.tenants

  # --- PostgreSQL: HA cluster ---
  pg_topology       = "cluster"
  pg_engine_version = var.pg_engine_version
  pg_package_name   = var.pg_package_name
  pg_volume_type    = var.pg_volume_type
  pg_volume_size    = var.pg_volume_size
  pg_cluster_nodes  = var.pg_cluster_nodes
  pg_username       = var.pg_username
  pg_password       = var.pg_password
  pg_db_name        = var.pg_db_name
  pg_public_access  = false
  pg_config_values = {
    max_connections = "200"
    autovacuum      = "true"
  }

  # --- In-DB bootstrap: run via CI/K8s job in prod (kept off here) ---
  run_db_bootstrap       = var.run_db_bootstrap
  app_role_password      = var.app_role_password
  db_bootstrap_extra_sql = var.db_bootstrap_extra_sql

  # --- Redis ---
  redis_engine_version = var.redis_engine_version
  redis_package_name   = var.redis_package_name
  redis_password       = var.redis_password
  redis_config_values = {
    "maxmemory-policy" = "allkeys-lru"
    "appendonly"       = "yes"
  }

  # --- Kafka: shared cluster, shared + per-tenant topics, scoped users ---
  # Producer keying convention (in the ingestion worker, not TF):
  #   key = "<tenant_id>:<identity-hint>" (external_customer_id / device_id /
  #   cookie_id / session_id / hashed-email) -> tenant co-location + per-visitor
  #   ordering. raw_profile_id is resolved server-side, not a produce-time key.
  kafka_enabled      = var.kafka_enabled
  kafka_package_name = var.kafka_package_name
  kafka_broker_count = var.kafka_broker_count
  kafka_sasl_authen  = true
  kafka_shared_topics = [
    # High-volume behavioral/transactional ingestion -> cdp_raw_events
    { name = "cdp.raw-events", partitions = 12, replicas = 3, retention_seconds = 1209600 },
    # Raw profiles from source systems + CRM/file imports -> cdp_raw_profiles_stage
    { name = "cdp.raw-profiles", partitions = 12, replicas = 3, retention_seconds = 1209600 },
    # Dead-letter queues (spec: DLQ + object-storage backup). 30d retention; DLQ
    # consumers mirror raw bytes to the vStorage "ingestion"/"backups" buckets.
    { name = "cdp.raw-events.dlq", partitions = 6, replicas = 3, retention_seconds = 2592000 },
    { name = "cdp.raw-profiles.dlq", partitions = 3, replicas = 3, retention_seconds = 2592000 },

    # Phase 2 (activation edge): emitted after CIR resolves a master profile;
    # consumed by segmentation/personalization/notification/email. Enable when the
    # data_synch producer exists. NOTE: log-compaction (cleanup.policy=compact,
    # keyed by tenant_id:master_profile_id) must be set via the Kafka config group
    # or console - the vngcloud_vdb_kafka_topic resource only exposes retention.
    # { name = "cdp.profile-resolved", partitions = 6, replicas = 3, retention_seconds = 2592000 },
  ]
  # Per-tenant hard-isolation ingestion edge (opt-in). Each becomes
  # "<tenant_code>.events" with a scoped "<tenant_code>-app" SASL user. A router
  # must fold these back into the cdp_raw_events pipeline (or run a dedicated
  # worker). Enable only for tenants with a contractual/regulatory isolation need.
  kafka_per_tenant_topics = [
    { name = "events", partitions = 6, replicas = 3, retention_seconds = 1209600 },
  ]

  # --- vStorage: versioned shared + per-tenant buckets ---
  vstorage_enabled            = var.vstorage_enabled
  vstorage_bucket_prefix      = var.vstorage_bucket_prefix
  vstorage_shared_buckets     = ["ingestion", "exports", "backups"]
  vstorage_per_tenant_buckets = ["assets"]
  vstorage_versioning         = true
}
