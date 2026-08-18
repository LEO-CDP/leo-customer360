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
  description = "Resolved id of the Ubuntu 24.04 image used for all servers."
  value       = data.vngcloud_vserver_image.this.id
}

output "root_disk_type_id" {
  description = "Resolved id of the root-disk volume type used for all servers."
  value       = data.vngcloud_vserver_volume_type.this.id
}

output "network_id" {
  value = local.network_id
}

output "subnet_id" {
  value = local.subnet_id
}
