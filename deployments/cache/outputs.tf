# Outputs describe the PROD managed MemStore. For uat (Docker container) there is
# no Terraform state — the endpoint is always 127.0.0.1:<redis_port> on the api box.

output "redis_host" {
  description = "Private IP of the managed MemStore (prod). Empty when deploy_managed=false."
  value       = try(vngcloud_vdb_memstore_database.this[0].ip[0], "")
}

output "redis_port" {
  description = "Port the managed MemStore listens on (prod)."
  value       = try(vngcloud_vdb_memstore_database.this[0].port, null)
}

output "redis_status" {
  value = try(vngcloud_vdb_memstore_database.this[0].status, "")
}

output "redis_package_id" {
  description = "Resolved id of the MemStore package matched from package_name."
  value       = try(data.vngcloud_vdb_database_package.redis[0].id, "")
}
