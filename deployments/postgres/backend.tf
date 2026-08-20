# Remote Terraform state on VNG vStorage (S3-compatible) so CI/CD can read the
# same state operators use locally. Credentials come from the environment
# (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) — never hardcode them here.
#
# One-time migration from the old local state (run locally, per module):
#   export AWS_ACCESS_KEY_ID=<vstorage key>  AWS_SECRET_ACCESS_KEY=<vstorage secret>
#   terraform -chdir=postgres init -migrate-state -force-copy
#
# Requires Terraform >= 1.6 (endpoints block, use_path_style, skip_s3_checksum)
# and that the state bucket already exists.
terraform {
  backend "s3" {
    bucket = "leocdp360-tfstate"          # vStorage bucket that HOLDS state (create once)
    key    = "postgres/terraform.tfstate" # per-module path; workspaces nest under env/
    region = "us-east-1"                  # vStorage requires us-east-1

    endpoints {
      s3 = "https://hcm04.vstorage.vngcloud.vn"
    }

    use_path_style              = true # S3-compatible: endpoint/bucket, not bucket.endpoint
    skip_credentials_validation = true # not real AWS — no STS
    skip_metadata_api_check     = true # no IMDS
    skip_region_validation      = true
    skip_requesting_account_id  = true # no IAM/account lookup
    skip_s3_checksum            = true # vStorage rejects the AWS default checksum

    workspace_key_prefix = "env" # workspace state at env/<workspace>/<key>
  }
}
