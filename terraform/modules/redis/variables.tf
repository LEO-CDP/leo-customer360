variable "name_prefix" {
  type        = string
  description = "Prefix applied to Redis resource names."
}

variable "zone_id" {
  type        = string
  description = "Availability zone (e.g. HCM03-1A)."
}

variable "subnet_id" {
  type        = string
  description = "Subnet the Redis instance is attached to."
}

variable "engine_version" {
  type        = string
  description = "Redis engine version accepted by vDB (e.g. 7.2). Verify against the console."
  default     = "7.2"
}

variable "package_name" {
  type        = string
  description = "vDB package (flavor) name, e.g. db.new.s-general-1x2."
  default     = "db.new.s-general-1x2"
}

variable "password" {
  type        = string
  description = "Redis requirepass value."
  sensitive   = true
}

variable "public_access" {
  type        = bool
  description = "Expose the instance with a public IP."
  default     = false
}

variable "allowed_ip_prefixes" {
  type        = list(string)
  description = "CIDRs allowed to reach Redis. Restrict this in prod."
  default     = ["0.0.0.0/0"]
}

variable "config_values" {
  type        = map(string)
  description = "Redis config group parameters (e.g. maxmemory-policy, appendonly). Empty = no config group."
  default     = {}
}

variable "backup_auto" {
  type        = bool
  description = "Enable daily automatic backups."
  default     = false
}

variable "backup_duration" {
  type        = number
  description = "Backup retention in days (2-14). Required by the API even when backup_auto is false."
  default     = 2
}

variable "backup_time" {
  type        = string
  description = "Daily backup time, e.g. 00:00."
  default     = "00:00"
}
