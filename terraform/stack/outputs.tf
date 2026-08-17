output "postgres" {
  description = "PostgreSQL connection info (DB_HOST/DB_PORT/DB_NAME for the app)."
  value = {
    topology    = module.postgres.topology
    instance_id = module.postgres.instance_id
    host        = module.postgres.host
    public_host = module.postgres.public_host
    ro_host     = module.postgres.ro_host
    port        = module.postgres.port
    ro_port     = module.postgres.ro_port
    db_name     = module.postgres.db_name
    username    = module.postgres.username
    # Cluster backup wiring (null for standalone) — verify post-apply.
    backup_policy_id   = module.postgres.backup_policy_id
    backup_location_id = module.postgres.backup_location_id
  }
}

output "redis" {
  description = "Redis connection info (REDIS_HOST/REDIS_PORT)."
  value = {
    instance_id = module.redis.instance_id
    host        = module.redis.host
    port        = module.redis.port
  }
}

output "kafka" {
  description = "Kafka cluster info (null when kafka_enabled = false)."
  value = var.kafka_enabled ? {
    cluster_id             = module.kafka[0].cluster_id
    broker_private_ips     = module.kafka[0].broker_private_ips
    broker_public_ips      = module.kafka[0].broker_public_ips
    shared_topic_names     = module.kafka[0].shared_topic_names
    per_tenant_topic_names = module.kafka[0].per_tenant_topic_names
    per_tenant_user_names  = module.kafka[0].per_tenant_user_names
  } : null
}

output "vstorage_buckets" {
  description = "Managed vStorage bucket names (null when vstorage_enabled = false)."
  value       = var.vstorage_enabled ? module.vstorage[0].bucket_names : null
}

output "network" {
  description = "Effective network/subnet the stack used."
  value = {
    network_id = local.effective_network_id
    subnet_id  = local.effective_subnet_id
    created    = var.create_network
  }
}
