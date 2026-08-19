terraform {
  required_version = ">= 1.3"

  required_providers {
    # VNG Cloud vStorage Object Storage is S3-COMPATIBLE and is NOT exposed by
    # the native vngcloud/vngcloud provider (that provider only covers vServer,
    # vDB, vLB, vKS). Buckets are therefore managed with the standard AWS
    # provider pointed at the vStorage S3 endpoint below.
    aws = {
      source = "hashicorp/aws"
      # Pinned to the v5 line; s3_use_path_style + the skip_* escape hatches
      # used here are stable across all of v5. Bump deliberately to v6.
      version = "~> 5.0"
    }
  }
}

# The AWS provider talks plain S3 to vStorage. Everything AWS-specific (STS,
# IMDS, account-id lookup, region allow-list) is switched OFF because this is
# not real AWS — only the S3 endpoint is honoured.
provider "aws" {
  access_key = var.access_key
  secret_key = var.secret_key
  region     = var.region

  # vStorage is not AWS: disable all the AWS-only preflight calls that would
  # otherwise fail (they hit AWS STS / IMDS / IAM, which don't exist here).
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
  skip_requesting_account_id  = true

  # S3-compatible stores use path-style addressing (endpoint/bucket), not the
  # AWS virtual-host style (bucket.endpoint).
  s3_use_path_style = true

  endpoints {
    s3 = var.s3_endpoint
  }
}
