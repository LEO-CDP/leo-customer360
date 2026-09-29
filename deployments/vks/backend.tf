# Remote Terraform state on VNG vStorage (S3-compatible), same bucket + conventions
# as the other modules. Credentials come from the environment
# (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) — never hardcode them here.
terraform {
  backend "s3" {
    bucket = "leocdp360-tfstate"     # vStorage bucket that HOLDS state (create once)
    key    = "vks/terraform.tfstate" # per-module path; workspaces nest under env/
    region = "us-east-1"             # vStorage requires us-east-1

    endpoints = {
      s3 = "https://hcm04.vstorage.vngcloud.vn"
    }

    use_path_style              = true
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true

    workspace_key_prefix = "env"
  }
}
