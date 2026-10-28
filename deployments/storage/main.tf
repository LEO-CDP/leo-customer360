# ---------------------------------------------------------------------------
# vStorage Object Storage buckets.
#
# vStorage speaks the S3 API, so buckets are plain aws_s3_bucket resources
# addressed through the custom endpoint configured in provider.tf. AWS-only
# sub-resources (public-access-block, ownership controls, ...) are intentionally
# omitted because vStorage does not implement those APIs; versioning IS
# supported and is wired up below behind var.enable_versioning.
# ---------------------------------------------------------------------------

locals {
  managed_bucket_names = toset(concat(
    var.bucket_names,
    [var.product_import_s3_bucket, var.content_import_s3_bucket],
  ))
}

resource "aws_s3_bucket" "this" {
  for_each = local.managed_bucket_names

  bucket = each.value

  # false during normal use so a bucket with objects can't be wiped by accident.
  # undeploy.sh --force passes -var force_destroy=true to empty + delete on teardown.
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_versioning" "this" {
  # Only manage versioning when explicitly enabled, so a minimal apply never
  # issues a PutBucketVersioning call.
  for_each = var.enable_versioning ? local.managed_bucket_names : toset([])

  bucket = aws_s3_bucket.this[each.value].id

  versioning_configuration {
    status = "Enabled"
  }
}
