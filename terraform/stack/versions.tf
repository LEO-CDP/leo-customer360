terraform {
  required_version = ">= 1.5.0"

  required_providers {
    vngcloud = {
      source  = "vngcloud/vngcloud"
      version = "~> 1.3" # kafka topic/user + postgresql_cluster require >= 1.3.x
    }
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0" # used only for vStorage (S3-compatible) buckets
    }
    null = {
      source = "hashicorp/null"
    }
  }
}
