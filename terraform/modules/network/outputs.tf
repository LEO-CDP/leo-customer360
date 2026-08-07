# These are null when create_network = false; the stack resolves the effective
# IDs with a coalesce() against the caller-provided values.
output "network_id" {
  description = "ID of the created network (null when create_network = false)."
  value       = one(vngcloud_vserver_network.this[*].id)
}

output "subnet_id" {
  description = "ID of the created subnet (null when create_network = false)."
  value       = one(vngcloud_vserver_subnet.this[*].id)
}
