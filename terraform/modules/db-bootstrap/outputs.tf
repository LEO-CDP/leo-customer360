output "bootstrap_id" {
  description = "ID of the bootstrap run resource (null when disabled)."
  value       = one(null_resource.bootstrap[*].id)
}
