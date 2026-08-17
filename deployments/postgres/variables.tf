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
  validation {
    condition     = length(var.instance_name) >= 6 && length(var.instance_name) <= 20
    error_message = "instance_name must be 6-20 characters (vDB limit)."
  }
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
  # vDB rule: start with a letter, do not start/end with a special char.
  validation {
    condition     = can(regex("^[A-Za-z].*[A-Za-z0-9]$", var.db_password)) && length(var.db_password) >= 8
    error_message = "db_password must start with a letter, end with a letter or digit (not a special char), and be at least 8 characters (vDB rule)."
  }
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
  default     = ""
  description = "Existing subnet id (sub-...). Used when create_network = false."
}

# --- Network creation (optional; set create_network = true to provision) ---
variable "create_network" {
  type        = bool
  default     = false
  description = "If true, create a VPC + subnet and attach the DB to it; if false, use var.subnet_id."
}

variable "project_id" {
  type        = string
  default     = ""
  description = "VNG Cloud project id (pro-...). Required when create_network = true; must be the project your credentials use."
}

variable "network_name" {
  type    = string
  default = "c360-vpc"
}

variable "network_cidr" {
  type        = string
  default     = "10.100.0.0/16"
  description = "VPC CIDR (/16; within 10.0.0.0-10.255.0.0, 172.16-172.24, or 192.168.0.0)."
}

variable "subnet_name" {
  type    = string
  default = "c360-subnet"
}

variable "subnet_cidr" {
  type        = string
  default     = "10.100.1.0/24"
  description = "Subnet CIDR; must be contained within network_cidr."
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
