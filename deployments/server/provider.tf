terraform {
  required_version = ">= 1.3"

  required_providers {
    vngcloud = {
      source = "vngcloud/vngcloud"
      # Pinned to allow patch updates only (>= 1.3.19, < 1.4.0).
      # Latest checked: 1.3.19 (2026-08-04). See:
      # https://registry.terraform.io/providers/vngcloud/vngcloud/latest
      version = "~> 1.3.19"
    }
  }
}

# GreenNode vServer runs on VNG Cloud's platform, so the IAM + gateway
# endpoints below are the VNG Cloud ones (GreenNode's own docs also
# authenticate against iamapis.vngcloud.vn). Override any *_base_url via
# the matching variable if your tenant was issued different hostnames.
provider "vngcloud" {
  client_id     = var.client_id
  client_secret = var.client_secret

  token_url        = var.token_url
  vserver_base_url = var.vserver_base_url
  vlb_base_url     = var.vlb_base_url
  vdb_base_url     = var.vdb_base_url
}
