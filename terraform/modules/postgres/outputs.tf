locals {
  standalone = one(vngcloud_vdb_relational_database.standalone)
  cluster    = one(vngcloud_vdb_postgresql_cluster.cluster)
}

output "instance_id" {
  description = "ID of the standalone instance or cluster."
  value       = local.is_cluster ? local.cluster.id : local.standalone.id
}

output "host" {
  description = "Private read-write host (DB_HOST)."
  value       = local.is_cluster ? local.cluster.private_rw_ip : try(element(tolist(local.standalone.ip), 0), null)
}

output "public_host" {
  description = "Public read-write host (only when public_access = true)."
  value       = local.is_cluster ? local.cluster.public_rw_ip : null
}

output "ro_host" {
  description = "Private read-only host (cluster only)."
  value       = local.is_cluster ? local.cluster.private_ro_ip : null
}

output "port" {
  description = "Read-write port (DB_PORT)."
  value       = local.is_cluster ? local.cluster.port : local.standalone.port
}

output "ro_port" {
  description = "Read-only port (cluster only)."
  value       = local.is_cluster ? local.cluster.port_ro : null
}

output "db_name" {
  value = var.db_name
}

output "username" {
  value = var.username
}

output "topology" {
  value = var.topology
}
