output "lb_id" {
  description = "ID of the created vLB load balancer."
  value       = vngcloud_vlb_load_balancer.this.id
}

output "lb_name" {
  value = vngcloud_vlb_load_balancer.this.name
}

output "lb_address" {
  description = "IP address of the load balancer (public when scheme = Internet)."
  value       = vngcloud_vlb_load_balancer.this.address
}

output "lb_status" {
  value = vngcloud_vlb_load_balancer.this.status
}

output "lb_private_subnet_cidr" {
  value = vngcloud_vlb_load_balancer.this.private_subnet_cidr
}

output "lb_package_id" {
  description = "Resolved uuid of the package matched from package_name."
  value       = local.lb_package_id
}

output "lb_packages" {
  description = "All vLB packages available in the project (name -> uuid, lb_type). Handy for finding the exact package_name to set."
  value       = data.vngcloud_vlb_lb_packages.all.packages
}
