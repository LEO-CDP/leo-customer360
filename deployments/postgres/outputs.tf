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

# --- Connection endpoint (consumed by run-sql.sh for post-deploy scripts) ---
output "db_ip" {
  description = "IP address(es) of the instance (private when public_access = false)."
  value       = vngcloud_vdb_relational_database.pg.ip
}

output "db_port" {
  description = "Port the database listens on."
  value       = vngcloud_vdb_relational_database.pg.port
}

output "db_host" {
  description = "Host run-sql.sh connects to — the PUBLIC ip if public_access exposed one, else the first ip."
  value = try(
    [for i in vngcloud_vdb_relational_database.pg.ip : i
    if length(regexall("^(10\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.|192\\.168\\.)", i)) == 0][0],
    try(vngcloud_vdb_relational_database.pg.ip[0], "")
  )
}

# --- Network ids (so a bastion in ../server can join THIS DB's subnet) ---
output "network_id" {
  description = "ID of the VPC created for the DB (empty if create_network = false)."
  value       = length(vngcloud_vserver_network.this) > 0 ? vngcloud_vserver_network.this[0].id : ""
}

output "subnet_id" {
  description = "ID of the subnet the DB lives in — put the bastion here to reach the private DB IP."
  value       = local.subnet_id
}
