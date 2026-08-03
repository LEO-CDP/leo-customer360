output "instance_id" {
  description = "vDB Memory store instance ID."
  value       = vngcloud_vdb_memstore_database.this.id
}

output "host" {
  description = "First IP address of the Redis instance (REDIS_HOST)."
  value       = try(element(tolist(vngcloud_vdb_memstore_database.this.ip), 0), null)
}

output "ips" {
  description = "All IP addresses associated with the Redis instance."
  value       = vngcloud_vdb_memstore_database.this.ip
}

output "port" {
  description = "Redis port (REDIS_PORT) assigned by vDB."
  value       = vngcloud_vdb_memstore_database.this.port
}

output "status" {
  value = vngcloud_vdb_memstore_database.this.status
}
