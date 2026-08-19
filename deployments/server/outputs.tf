output "servers" {
  description = "Provisioned API servers keyed by the servers-map key: id, name, and network interfaces (IP/MAC)."
  value = {
    for k, s in vngcloud_vserver_server.this : k => {
      id                  = s.id
      name                = s.name
      internal_interfaces = s.internal_interfaces
      external_interfaces = s.external_interfaces
    }
  }
}

output "flavors" {
  description = "Resolved CPU/RAM per server flavor (sanity-check the s2-general tiers)."
  value = {
    for k, f in data.vngcloud_vserver_flavor.this : k => {
      flavor_id = f.id
      cpu       = f.cpu
      memory_gb = f.memory
    }
  }
}

output "image_id" {
  description = "Resolved id of the image used for all servers (direct var.image_id or the name lookup)."
  value       = local.image_id
}

output "root_disk_type_id" {
  description = "Resolved id of the root-disk volume type used for all servers."
  value       = local.root_disk_type_id
}

output "network_id" {
  value = local.network_id
}

output "subnet_id" {
  value = local.subnet_id
}
