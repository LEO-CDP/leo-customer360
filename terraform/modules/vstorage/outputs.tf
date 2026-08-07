output "bucket_names" {
  description = "All managed bucket names, keyed by logical key."
  value       = { for k, b in aws_s3_bucket.this : k => b.bucket }
}

output "shared_bucket_names" {
  value = [for k, v in local.shared : v]
}

output "per_tenant_bucket_names" {
  value = [for k, v in local.per_tenant : v]
}
