# DEV environment: single-node PostgreSQL, one shared Redis, one shared Kafka
# cluster (shared topics keyed by tenant_id), and vStorage buckets (shared +
# per-tenant). Tenant isolation is in-data (Postgres RLS), matching the app.

module "stack" {
  source = "../../stack"

  # When a module receives an explicit providers map, default inheritance is
  # disabled, so every provider the stack uses must be listed here.
  providers = {
    vngcloud = vngcloud
    aws      = aws
    null     = null
  }

  environment = "dev"
  name_prefix = var.name_prefix

  zone_id            = var.zone_id
  vserver_project_id = var.vserver_project_id

  # --- Networking: reference existing by default ---
  create_network         = var.create_network
  network_id             = var.network_id
  subnet_id              = var.subnet_id
  network_cidr           = var.network_cidr
  subnet_cidr            = var.subnet_cidr
  db_allowed_ip_prefixes = var.db_allowed_ip_prefixes

  tenants = var.tenants

  # --- PostgreSQL: standalone single node for dev ---
  pg_topology       = "standalone"
  pg_engine_version = var.pg_engine_version
  pg_package_name   = var.pg_package_name
  pg_volume_type    = "Gen2-NVMe2-IOPS5000"
  pg_volume_size    = 20
  pg_username       = var.pg_username
  pg_password       = var.pg_password
  pg_db_name        = var.pg_db_name
  pg_public_access  = var.pg_public_access
  pg_config_values  = { max_connections = "100" }

  # --- In-DB bootstrap (extensions + RLS role + schema + seed) ---
  run_db_bootstrap       = var.run_db_bootstrap
  app_role_password      = var.app_role_password
  db_bootstrap_extra_sql = var.db_bootstrap_extra_sql

  # --- Redis (parity with repo redis.conf) ---
  redis_engine_version = var.redis_engine_version
  redis_package_name   = var.redis_package_name
  redis_password       = var.redis_password
  redis_config_values = {
    "maxmemory-policy" = "allkeys-lru"
    "appendonly"       = "yes"
  }

  # --- Kafka: shared cluster + shared topics (tenant_id in the key) ---
  # Producer keying convention (set in the ingestion worker, not in TF):
  #   key = "<tenant_id>:<identity-hint>"  where identity-hint = first present of
  #   external_customer_id / device_id / cookie_id / session_id / hashed-email.
  # This co-locates a tenant's data AND keeps one visitor's event timeline ordered
  # within a partition. raw_profile_id is resolved server-side, so it cannot be the
  # produce-time key.
  kafka_enabled      = var.kafka_enabled
  kafka_package_name = var.kafka_package_name
  kafka_broker_count = 3
  kafka_sasl_authen  = true
  kafka_shared_topics = [
    # Raw behavioral/transactional events -> cdp_raw_events
    { name = "cdp.raw-events", partitions = 6, replicas = 3, retention_seconds = 604800 },
    # Raw profiles from source systems + CRM/file imports -> cdp_raw_profiles_stage
    { name = "cdp.raw-profiles", partitions = 6, replicas = 3, retention_seconds = 604800 },
    # Dead-letter queues (design requires DLQ + object-storage backup). Longer
    # retention for inspect/replay; DLQ consumers also mirror raw bytes to the
    # vStorage "events" bucket. Keyed by the original tenant_id.
    { name = "cdp.raw-events.dlq", partitions = 3, replicas = 3, retention_seconds = 1209600 },
    { name = "cdp.raw-profiles.dlq", partitions = 3, replicas = 3, retention_seconds = 1209600 },
  ]
  kafka_per_tenant_topics = []

  # --- vStorage (S3) buckets ---
  # The app reads exactly ONE object-storage bucket today, via MINIO_BUCKET
  # (k8s c360-config, default "customer360-events-dev") - used by the file-based
  # event-ingestion path / all-data-simulator's MinIO client. So dev provisions
  # just that bucket. Wire the k8s vks overlay's MINIO_BUCKET to the name below
  # ("<vstorage_bucket_prefix>events", i.e. "c360-dev-events").
  # exports/backups/per-tenant "assets" are forward-looking (no code consumes
  # them yet) - add them here when those features land.
  vstorage_enabled            = var.vstorage_enabled
  vstorage_bucket_prefix      = var.vstorage_bucket_prefix
  vstorage_shared_buckets     = ["events"]
  vstorage_per_tenant_buckets = []
  vstorage_versioning         = false
}
