variable "name_prefix" {
  type        = string
  description = "Prefix applied to Kafka resource names."
}

variable "project_id" {
  type        = string
  description = "VNG vServer project ID (pro-xxxx)."
}

variable "network_id" {
  type        = string
  description = "Network the cluster is attached to."
}

variable "subnet_id" {
  type        = string
  description = "Subnet the broker nodes launch in."
}

variable "kafka_version" {
  type        = string
  description = "Kafka version (e.g. 3.7.0)."
  default     = "3.7.0"
}

variable "package_name" {
  type        = string
  description = "vDB Kafka package name, e.g. db-kafka.s-general-2x4-n10."
}

variable "volume_type" {
  type        = string
  description = "Broker volume type, e.g. Gen2-NVMe2-IOPS3000."
  default     = "Gen2-NVMe2-IOPS3000"
}

variable "storage_size" {
  type        = number
  description = "Broker volume size in GB."
  default     = 20
}

variable "broker_count" {
  type        = number
  description = "Number of broker nodes (3-10)."
  default     = 3
}

variable "mtls_authen" {
  type        = bool
  description = "Enable mTLS authentication."
  default     = false
}

variable "sasl_authen" {
  type        = bool
  description = "Enable SASL authentication (required for per-tenant users)."
  default     = true
}

variable "public_access" {
  type        = bool
  description = "Assign floating IPs to brokers."
  default     = false
}

variable "encryption_volume" {
  type        = bool
  description = "Encrypt broker volumes at rest."
  default     = false
}

variable "allowed_ip_prefixes" {
  type        = list(string)
  description = "CIDRs allowed to reach the brokers. Restrict this in prod."
  default     = ["0.0.0.0/0"]
}

variable "config_properties" {
  type        = map(string)
  description = "Kafka config group properties (e.g. default.replication.factor). Empty = none."
  default     = {}
}

variable "tenants" {
  type = list(object({
    id   = string
    code = string
  }))
  description = "Tenants driving per-tenant topics and users."
  default     = []
}

variable "shared_topics" {
  type = list(object({
    name              = string
    partitions        = number
    replicas          = number
    retention_seconds = optional(number)
    retention_bytes   = optional(number)
  }))
  description = "Cross-tenant topics keyed by tenant_id in the payload."
  default     = []
}

variable "per_tenant_topics" {
  type = list(object({
    name              = string
    partitions        = number
    replicas          = number
    retention_seconds = optional(number)
    retention_bytes   = optional(number)
  }))
  description = "Topic templates instantiated per tenant as <tenant_code>.<name>."
  default     = []
}
