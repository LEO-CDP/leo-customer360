# Remote Terraform state on VNG vStorage (S3-compatible) so CI/CD and operators
# share one state. Credentials come from the environment (AWS_ACCESS_KEY_ID /
# AWS_SECRET_ACCESS_KEY) — never hardcode them here.
#
# One-time migration from the old local state (run locally):
#   export AWS_ACCESS_KEY_ID=<vstorage key>  AWS_SECRET_ACCESS_KEY=<vstorage secret>
#   terraform -chdir=load_balancer init -migrate-state -force-copy
#
# Requires Terraform >= 1.6 (endpoints attribute, use_path_style, skip_s3_checksum).
# NOTE: vStorage does not enforce S3 conditional PUT (If-None-Match), so native
# state locking (use_lockfile) is NOT reliable here — avoid concurrent applies.
terraform {
  backend "s3" {
    bucket = "leocdp360-tfstate"
    key    = "load_balancer/terraform.tfstate"
    region = "us-east-1"

    endpoints = { s3 = "https://hcm04.vstorage.vngcloud.vn" }

    use_path_style              = true
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true

    workspace_key_prefix = "env"
  }
}
