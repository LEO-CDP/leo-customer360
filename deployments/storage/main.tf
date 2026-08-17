# ---------------------------------------------------------------------------
# vStorage Object Storage buckets.
#
# vStorage speaks the S3 API, so buckets are plain aws_s3_bucket resources
# addressed through the custom endpoint configured in provider.tf. AWS-only
# sub-resources (public-access-block, ownership controls, ...) are intentionally
# omitted because vStorage does not implement those APIs; versioning IS
# supported and is wired up below behind var.enable_versioning.
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "this" {
  for_each = toset(var.bucket_names)

  bucket = each.value
}

resource "aws_s3_bucket_versioning" "this" {
  # Only manage versioning when explicitly enabled, so a minimal apply never
  # issues a PutBucketVersioning call.
  for_each = var.enable_versioning ? toset(var.bucket_names) : toset([])

  bucket = aws_s3_bucket.this[each.value].id

  versioning_configuration {
    status = "Enabled"
  }
}
