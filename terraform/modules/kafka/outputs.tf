output "cluster_id" {
  description = "Kafka cluster ID."
  value       = vngcloud_vdb_kafka_cluster.this.id
}

output "broker_private_ips" {
  description = "Internal broker IPs."
  value       = vngcloud_vdb_kafka_cluster.this.fixed_ips
}

output "broker_public_ips" {
  description = "Public broker IPs (only when public_access = true)."
  value       = vngcloud_vdb_kafka_cluster.this.floating_ips
}

output "status" {
  value = vngcloud_vdb_kafka_cluster.this.status
}

output "shared_topic_names" {
  value = [for t in vngcloud_vdb_kafka_topic.shared : t.name]
}

output "per_tenant_topic_names" {
  value = [for t in vngcloud_vdb_kafka_topic.per_tenant : t.name]
}

output "per_tenant_user_names" {
  value = [for u in vngcloud_vdb_kafka_user.per_tenant : u.name]
}
