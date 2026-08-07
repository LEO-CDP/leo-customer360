terraform {
  required_providers {
    # vStorage has NO native vngcloud resource. It is S3-compatible, so we manage
    # buckets with the AWS provider pointed at the vStorage S3 endpoint. The aws
    # provider (endpoint + S3 access/secret key + path-style) is configured in the
    # environment and passed in here.
    aws = {
      source = "hashicorp/aws"
    }
  }
}

locals {
  # Shared buckets (one per name), prefixed.
  shared = { for b in var.shared_buckets : b => "${var.bucket_prefix}${b}" }

  # Per-tenant buckets: one per (tenant x per_tenant_buckets), named
  # "<prefix><tenant_code>-<bucket>".
  per_tenant = {
    for pair in setproduct(var.tenants, var.per_tenant_buckets) :
    "${pair[0].code}-${pair[1]}" => "${var.bucket_prefix}${pair[0].code}-${pair[1]}"
  }

  all_buckets = merge(local.shared, local.per_tenant)
}

resource "aws_s3_bucket" "this" {
  for_each = local.all_buckets
  bucket   = each.value
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = var.versioning ? aws_s3_bucket.this : {}
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}
