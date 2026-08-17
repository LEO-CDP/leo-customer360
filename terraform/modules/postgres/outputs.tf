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

output "backup_policy_id" {
  description = "Effective Backup Policy ID (cluster only; null for standalone)."
  value       = local.is_cluster ? try(local.cluster.backup_policy_id, null) : null
}

output "backup_location_id" {
  description = "Effective Backup Location ID (cluster only; null for standalone)."
  value       = local.is_cluster ? try(local.cluster.backup_location_id, null) : null
}
