terraform {
  required_providers {
    vngcloud = {
      source = "vngcloud/vngcloud"
    }
  }
}

# Managed Kafka on vDB. Kafka is greenfield for Customer360 (no producers/
# consumers exist yet - ingestion is REST -> Postgres today). This module stands
# up the shared cluster plus a hybrid topic/user layout matching the app's
# "shared infra + tenant_id" convention:
#
#   * shared topics  -> messages carry tenant_id in the key/payload (cross-tenant
#                       streams such as raw event ingestion).
#   * per-tenant topics + per-tenant SASL users -> hard isolation at the stream
#                       edge for tenants that need it (hybrid model).

data "vngcloud_vdb_kafka_package" "this" {
  name = var.package_name
}

data "vngcloud_vdb_kafka_volume_type" "this" {
  type = var.volume_type
}

resource "vngcloud_vdb_kafka_config_group" "this" {
  count = length(var.config_properties) > 0 ? 1 : 0

  name        = "${var.name_prefix}-kafka-cfg"
  description = "Managed by Terraform"
  properties  = var.config_properties
}

resource "vngcloud_vdb_kafka_cluster" "this" {
  name               = "${var.name_prefix}-kafka"
  kafka_version      = var.kafka_version
  server_flavor_id   = data.vngcloud_vdb_kafka_package.this.id
  kafka_broker_count = var.broker_count
  kafka_storage_type = data.vngcloud_vdb_kafka_volume_type.this.id
  kafka_storage_size = var.storage_size

  vserver_project_id = var.project_id
  network_id         = var.network_id
  subnet_id          = var.subnet_id

  mtls_authen       = var.mtls_authen
  sasl_authen       = var.sasl_authen
  public_access     = var.public_access
  encryption_volume = var.encryption_volume

  config_group_version_id = length(var.config_properties) > 0 ? vngcloud_vdb_kafka_config_group.this[0].current_version_id : null
  auto_rebalance_topics   = true

  # SASL_PLAINTEXT/SSL listener 9094 when SASL is on, PLAINTEXT 9092 otherwise.
  dynamic "security_group_rules" {
    for_each = var.allowed_ip_prefixes
    content {
      remote_ip = security_group_rules.value
      port      = var.sasl_authen ? 9094 : 9092
    }
  }
}

# --------------------------------------------------------------------------
# Shared topics (tenant_id lives in the message key/payload)
# --------------------------------------------------------------------------
resource "vngcloud_vdb_kafka_topic" "shared" {
  for_each = { for t in var.shared_topics : t.name => t }

  cluster_id        = vngcloud_vdb_kafka_cluster.this.id
  name              = each.value.name
  partitions        = each.value.partitions
  replicas          = each.value.replicas
  retention_seconds = each.value.retention_seconds
  retention_bytes   = each.value.retention_bytes
}

# --------------------------------------------------------------------------
# Per-tenant topics: one topic per (tenant x per_tenant_topics template),
# named "<tenant_code>.<topic>".
# --------------------------------------------------------------------------
resource "vngcloud_vdb_kafka_topic" "per_tenant" {
  for_each = {
    for pair in setproduct(var.tenants, var.per_tenant_topics) :
    "${pair[0].code}.${pair[1].name}" => {
      tenant = pair[0]
      topic  = pair[1]
    }
  }

  cluster_id        = vngcloud_vdb_kafka_cluster.this.id
  name              = "${each.value.tenant.code}.${each.value.topic.name}"
  partitions        = each.value.topic.partitions
  replicas          = each.value.topic.replicas
  retention_seconds = each.value.topic.retention_seconds
  retention_bytes   = each.value.topic.retention_bytes
}

# --------------------------------------------------------------------------
# Per-tenant SASL app users, scoped to that tenant's own topics only.
# Created only when SASL auth is enabled and per-tenant topics exist.
# --------------------------------------------------------------------------
resource "vngcloud_vdb_kafka_user" "per_tenant" {
  for_each = (var.sasl_authen && length(var.per_tenant_topics) > 0) ? { for t in var.tenants : t.code => t } : {}

  cluster_id  = vngcloud_vdb_kafka_cluster.this.id
  name        = "${each.value.code}-app"
  sasl_authen = true

  produce_consume_topic_names = [
    for tp in var.per_tenant_topics : "${each.value.code}.${tp.name}"
  ]

  depends_on = [vngcloud_vdb_kafka_topic.per_tenant]
}
