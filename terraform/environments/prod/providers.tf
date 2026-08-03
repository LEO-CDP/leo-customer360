terraform {
  required_version = ">= 1.5.0"

  required_providers {
    vngcloud = {
      source  = "vngcloud/vngcloud"
      version = "~> 1.3"
    }
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    null = {
      source = "hashicorp/null"
    }
  }

  # Use remote state in prod. Example keeping state in vStorage (S3-compatible):
  # backend "s3" {
  #   bucket                      = "c360-tfstate"
  #   key                         = "prod/terraform.tfstate"
  #   region                      = "hcm03"
  #   endpoints                   = { s3 = "https://hcm03.vstorage.vngcloud.vn" }
  #   skip_credentials_validation = true
  #   skip_requesting_account_id  = true
  #   skip_metadata_api_check     = true
  #   skip_region_validation      = true
  #   use_path_style              = true
  # }
}

provider "vngcloud" {
  token_url        = var.vng_token_url
  client_id        = var.vng_client_id
  client_secret    = var.vng_client_secret
  vserver_base_url = var.vng_vserver_base_url
  vlb_base_url     = var.vng_vlb_base_url
  vdb_base_url     = var.vng_vdb_base_url
}

provider "aws" {
  access_key = var.vstorage_access_key
  secret_key = var.vstorage_secret_key
  region     = var.vstorage_region

  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
  s3_use_path_style           = true

  endpoints {
    s3 = var.vstorage_s3_endpoint
  }
}
