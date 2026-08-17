output "db_instance_id" {
  description = "ID of the created vDB PostgreSQL instance."
  value       = vngcloud_vdb_relational_database.pg.id
}

output "db_instance_name" {
  value = vngcloud_vdb_relational_database.pg.name
}

output "db_name" {
  value = vngcloud_vdb_relational_database.pg.db_name
}

output "package_cpu" {
  description = "vCPU count of the selected package."
  value       = data.vngcloud_vdb_database_package.pg.cpu
}

output "package_ram_gb" {
  description = "RAM (GB) of the selected package."
  value       = data.vngcloud_vdb_database_package.pg.ram
}
