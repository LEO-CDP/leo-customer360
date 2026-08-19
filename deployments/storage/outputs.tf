output "bucket_names" {
  description = "Names of the created vStorage buckets."
  value       = [for b in aws_s3_bucket.this : b.id]
}

output "bucket_domain_names" {
  description = "Path-style base URLs to reach each bucket."
  value       = { for name, b in aws_s3_bucket.this : name => "${var.s3_endpoint}/${b.id}" }
}

output "s3_endpoint" {
  description = "vStorage S3 endpoint the buckets live behind."
  value       = var.s3_endpoint
}

output "versioning_enabled" {
  value = var.enable_versioning
}

# --- Cost estimate (VND / month) ---
output "estimated_monthly_cost_vnd" {
  description = "Estimated monthly vStorage bill (VND): storage + bandwidth."
  value       = local.est_total_monthly_vnd
}

output "estimated_cost_breakdown" {
  description = "Line items behind the monthly cost estimate (VND)."
  value       = local.cost_breakdown
}

output "estimated_cost_summary" {
  description = "Human-readable cost estimate."
  value = format(
    "Storage %g TB = %d VND + Bandwidth %g GB = %d VND => %d VND/month",
    var.estimated_storage_tb,
    local.est_storage_cost_vnd,
    var.estimated_bandwidth_gb,
    local.est_bandwidth_cost_vnd,
    local.est_total_monthly_vnd,
  )
}
