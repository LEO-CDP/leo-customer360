# ---------------------------------------------------------------------------
# Authentication (vIAM service account) — only needed for the PROD (managed
# MemStore) path. The UAT path is a Docker container and uses none of this.
# ---------------------------------------------------------------------------
variable "client_id" {
  type        = string
  default     = ""
  description = "vIAM service-account client id. Required only for the prod managed-MemStore path."
}

variable "client_secret" {
  type        = string
  default     = ""
  sensitive   = true
  description = "vIAM service-account client secret. Required only for the prod managed-MemStore path."
}

variable "token_url" {
  type    = string
  default = "https://iamapis.vngcloud.vn/accounts-api/v2/auth/token"
}

variable "vserver_base_url" {
  type    = string
  default = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
}

variable "vlb_base_url" {
  type    = string
  default = "https://hcm-3.api.vngcloud.vn/vserver/vlb-gateway"
}

variable "vdb_base_url" {
  type    = string
  default = "https://vdb-gateway.vngcloud.vn"
}

# ---------------------------------------------------------------------------
# Shared: the Redis AUTH password. Used by BOTH the uat Docker container and the
# prod managed MemStore, and by customer360-api (REDIS_PASSWORD). Keep it secret
# (terraform.tfvars / .env as TF_VAR_redis_password); never commit it.
# ---------------------------------------------------------------------------
variable "redis_password" {
  type        = string
  sensitive   = true
  description = "Redis AUTH password (requirepass). Shared with customer360-api's REDIS_PASSWORD."
  validation {
    condition     = length(var.redis_password) >= 12
    error_message = "redis_password must be at least 12 characters."
  }
}

# ---------------------------------------------------------------------------
# UAT path — Redis as a Docker container ON the api server VM. These are read by
# deploy.sh (via grep), NOT by Terraform; deploy.sh uat never runs Terraform.
# ---------------------------------------------------------------------------
variable "redis_port" {
  type        = number
  default     = 6580 # matches customer360-api's REDIS_PORT default (core/config.py)
  description = "Port Redis listens on. 6580 keeps parity with the app default + docker-compose."
}

variable "redis_image" {
  type        = string
  default     = "customer360-redis:local"
  description = "Image tag for the uat Redis. Built from redis_build_context when set (the repo ./redis: redis:8-alpine + cache-tuned redis.conf), else pulled."
}

variable "redis_build_context" {
  type        = string
  default     = "../../redis"
  description = "Path (relative to this deployment) to a Docker build context for the Redis image. The repo ./redis image (redis.conf: port 6580, appendonly, maxmemory 256mb allkeys-lru) matches docker-compose. Empty = pull redis_image instead of building."
}

variable "api_server_key" {
  type        = string
  default     = "api"
  description = "for_each key in ../server of the VM to host the uat Redis container on (co-located with customer360-api)."
}

# ---------------------------------------------------------------------------
# PROD path — managed VNG MemStore (Redis). Only created when deploy_managed=true.
# ---------------------------------------------------------------------------
variable "deploy_managed" {
  type        = bool
  default     = true
  description = "Create the managed MemStore instance. Set false for envs that use the Docker container instead (uat)."
}

variable "instance_name" {
  type    = string
  default = "c360-redis-prod"
}

variable "engine_version" {
  type        = string
  default     = "7.0"
  description = "Redis engine version. Confirm an AVAILABLE version in the console MemStore create form — a wrong value yields no package match."
}

variable "package_name" {
  type        = string
  default     = "db.s-general-2x4"
  description = "MemStore package (CPU/RAM tier) as shown in the console create dropdown, e.g. db.s-general-2x4 (2 vCPU / 4 GB)."
}

variable "subnet_id" {
  type        = string
  default     = ""
  description = "Existing subnet id (sub-...) the MemStore lives in — the SAME subnet as the prod api server so it is reachable privately."
}

variable "zone_id" {
  type    = string
  default = "HCM03-1C"
  validation {
    condition     = contains(["HCM03-1A", "HCM03-1B", "HCM03-1C"], var.zone_id)
    error_message = "zone_id must be one of HCM03-1A, HCM03-1B, HCM03-1C."
  }
}

variable "public_access" {
  type        = bool
  default     = false
  description = "Expose the MemStore publicly. Keep false — the api reaches it privately in-VPC (public vDB access is non-functional on this platform anyway)."
}

variable "allowed_ip_prefix" {
  type        = list(string)
  default     = ["10.100.0.0/16"] # the VPC CIDR — private, in-VPC clients only
  description = "CIDRs allowed to reach the MemStore. Default = the VPC only."
}

variable "backup_auto" {
  type    = bool
  default = true
}

variable "backup_duration" {
  type        = number
  default     = 2
  description = "Backup retention in days (min 2, max 14)."
}

variable "backup_time" {
  type    = string
  default = "00:00"
}
