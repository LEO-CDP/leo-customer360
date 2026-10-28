terraform {
  required_version = ">= 1.3"

  required_providers {
    vngcloud = {
      source  = "vngcloud/vngcloud"
      version = "~> 1.3.19"
    }
  }
}

# Same credentials as ../server (a vIAM service account).
provider "vngcloud" {
  client_id     = var.client_id
  client_secret = var.client_secret

  token_url        = var.token_url
  vserver_base_url = var.vserver_base_url
}
