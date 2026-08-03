variable "bucket_prefix" {
  type        = string
  description = "Global prefix for every bucket name (bucket names are globally unique within vStorage). E.g. \"c360-dev-\"."
  default     = ""
}

variable "tenants" {
  type = list(object({
    id   = string
    code = string
  }))
  description = "Tenants driving per-tenant buckets."
  default     = []
}

variable "shared_buckets" {
  type        = list(string)
  description = "Shared bucket base names (e.g. ingestion, exports, backups)."
  default     = []
}

variable "per_tenant_buckets" {
  type        = list(string)
  description = "Per-tenant bucket base names, instantiated as <prefix><tenant_code>-<name>."
  default     = []
}

variable "versioning" {
  type        = bool
  description = "Enable object versioning on all managed buckets."
  default     = false
}
