terraform {
  required_version = ">= 1.3"

  required_providers {
    vngcloud = {
      source  = "vngcloud/vngcloud"
      version = "~> 1.3.19"
    }
  }
}

# Only used by the PROD path (managed MemStore). The UAT path is a Docker
# container deployed over SSH by deploy.sh and does not touch Terraform.
provider "vngcloud" {
  client_id     = var.client_id
  client_secret = var.client_secret

  token_url        = var.token_url
  vserver_base_url = var.vserver_base_url
  vlb_base_url     = var.vlb_base_url
  vdb_base_url     = var.vdb_base_url
}
