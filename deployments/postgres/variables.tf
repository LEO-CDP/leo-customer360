# ---------------------------------------------------------------------------
# Authentication (from GreenNode/VNG Cloud console -> IAM -> Service Account)
# ---------------------------------------------------------------------------
variable "client_id" {
  type        = string
  description = "vIAM service-account client id."
}

variable "client_secret" {
  type        = string
  description = "vIAM service-account client secret."
  sensitive   = true
}

# ---------------------------------------------------------------------------
# Platform endpoints (defaults target VNG Cloud, which GreenNode runs on)
# ---------------------------------------------------------------------------
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
# Database instance
# ---------------------------------------------------------------------------
variable "instance_name" {
  type    = string
  default = "leo-customer360-pg"
}

variable "db_name" {
  type    = string
  default = "customer360"
}

variable "db_username" {
  type    = string
  default = "app_admin"
}

variable "db_password" {
  type        = string
  description = "Master DB password. Do NOT commit a real value."
  sensitive   = true
}

variable "engine_version" {
  type        = string
  default     = "16"
  description = "PostgreSQL major version; must match a value offered by the console."
}

variable "package_name" {
  type        = string
  default     = "db.s-general-1x2"
  description = "Compute tier (CPU/RAM). Copy the exact name from the console dropdown."
}

variable "volume_type" {
  type    = string
  default = "Gen2-NVMe2-IOPS3000"
}

variable "volume_size" {
  type        = number
  default     = 20
  description = "Storage size in GB."
}

variable "subnet_id" {
  type        = string
  description = "VPC subnet the DB instance is attached to (e.g. sub-xxxxxxxx-...)."
}

variable "zone_id" {
  type    = string
  default = "HCM03-1A"
  validation {
    condition     = contains(["HCM03-1A", "HCM03-1B", "HCM03-1C"], var.zone_id)
    error_message = "zone_id must be one of HCM03-1A, HCM03-1B, HCM03-1C."
  }
}

variable "public_access" {
  type    = bool
  default = false
}

variable "allowed_ip_prefix" {
  type        = list(string)
  default     = ["10.0.0.0/8"]
  description = "CIDRs allowed to reach the instance. Tighten to your app's range."
}

variable "backup_auto" {
  type    = bool
  default = true
}

variable "backup_time" {
  type    = string
  default = "00:00"
}

variable "backup_duration" {
  type        = number
  default     = 7
  description = "Backup retention in days (2-14). Required when backup_auto = true."
}
