# ---------------------------------------------------------------------------
# General
# ---------------------------------------------------------------------------
variable "environment" {
  type        = string
  description = "Environment name (dev, staging, prod). Used in resource names."
}

variable "name_prefix" {
  type        = string
  description = "Base name prefix. Final prefix is \"<name_prefix>-<environment>\"."
  default     = "c360"
}

variable "zone_id" {
  type        = string
  description = "Availability zone (HCM03-1A / -1B / -1C)."
  default     = "HCM03-1A"
}

variable "vserver_project_id" {
  type        = string
  description = "VNG vServer project ID (pro-xxxx). Required for Kafka and for creating networks."
}

# ---------------------------------------------------------------------------
# Networking (support both: reference existing by default, or create)
# ---------------------------------------------------------------------------
variable "create_network" {
  type        = bool
  description = "Create a dedicated network + subnet instead of referencing existing IDs."
  default     = false
}

variable "network_id" {
  type        = string
  description = "Existing network ID (net-xxxx). Used when create_network = false."
  default     = ""
}

variable "subnet_id" {
  type        = string
  description = "Existing subnet ID (sub-xxxx). Used when create_network = false."
  default     = ""
}

variable "network_cidr" {
  type        = string
  description = "CIDR for a created network."
  default     = "10.76.0.0/16"
}

variable "subnet_cidr" {
  type        = string
  description = "CIDR for a created subnet."
  default     = "10.76.1.0/24"
}

variable "db_allowed_ip_prefixes" {
  type        = list(string)
  description = "CIDRs allowed to reach the data services. Restrict in prod."
  default     = ["0.0.0.0/0"]
}

# ---------------------------------------------------------------------------
# Tenants (drive hybrid per-tenant Kafka topics/users and vStorage buckets)
# ---------------------------------------------------------------------------
variable "tenants" {
  type = list(object({
    id   = string # tenant UUID (matches sys_tenant.tenant_id)
    code = string # short slug used in topic/bucket names (dns/kafka safe)
  }))
  description = "Tenant list for per-tenant resources."
  default     = []
}

# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------
variable "pg_topology" {
  type        = string
  description = "standalone (dev/PoC) or cluster (HA prod)."
  default     = "standalone"
}

variable "pg_engine_version" {
  type    = string
  default = "16"
}

variable "pg_package_name" {
  type        = string
  description = "vDB package. Standalone: db.* (e.g. db.new.s-general-2x4). Cluster: vdb.* (e.g. vdb.s-general-2x4)."
}

variable "pg_volume_type" {
  type    = string
  default = "Gen2-NVMe-IOPS5000"
}

variable "pg_volume_size" {
  type    = number
  default = 20
}

variable "pg_cluster_nodes" {
  type        = number
  description = "Node count when pg_topology = cluster (2-10)."
  default     = 3
}

variable "pg_username" {
  type    = string
  default = "postgres"
}

variable "pg_password" {
  type      = string
  sensitive = true
}

variable "pg_db_name" {
  type    = string
  default = "customer360"
}

variable "pg_public_access" {
  type        = bool
  description = "Expose Postgres publicly (needed if db-bootstrap runs from outside the VPC)."
  default     = false
}

variable "pg_config_values" {
  type        = map(string)
  description = "PostgreSQL config group params (e.g. { max_connections = \"200\" })."
  default     = {}
}

# ---------------------------------------------------------------------------
# In-database bootstrap (extensions + RLS role + schema + seed)
# ---------------------------------------------------------------------------
variable "run_db_bootstrap" {
  type        = bool
  description = "Run schema/role/seed via psql after provisioning. Needs psql+bash+reachability."
  default     = false
}

variable "db_schema" {
  type    = string
  default = "customer360"
}

variable "create_keycloak_db" {
  type        = bool
  description = "When run_db_bootstrap is on, also create the dedicated db_keycloak database the app's KC_DB_URL points at (parity with docker-compose keycloak-db-init / k8s)."
  default     = true
}

variable "app_role_name" {
  type    = string
  default = "customer360_app"
}

variable "app_role_password" {
  type      = string
  sensitive = true
  default   = ""
}

variable "db_bootstrap_extra_sql" {
  type        = list(string)
  description = "Extra SQL files (paths) applied last, e.g. the LLM materialized views."
  default     = []
}

# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------
variable "redis_engine_version" {
  type    = string
  default = "7.2"
}

variable "redis_package_name" {
  type    = string
  default = "db.new.s-general-1x2"
}

variable "redis_password" {
  type      = string
  sensitive = true
}

variable "redis_public_access" {
  type    = bool
  default = false
}

variable "redis_config_values" {
  type        = map(string)
  description = "Redis config group params, e.g. { maxmemory-policy = \"allkeys-lru\", appendonly = \"yes\" }."
  default     = {}
}

# ---------------------------------------------------------------------------
# Kafka
# ---------------------------------------------------------------------------
variable "kafka_enabled" {
  type    = bool
  default = true
}

variable "kafka_version" {
  type    = string
  default = "3.7.0"
}

variable "kafka_package_name" {
  type    = string
  default = "db-kafka.s-general-2x4-n10"
}

variable "kafka_volume_type" {
  type    = string
  default = "Gen2-NVMe2-IOPS3000"
}

variable "kafka_storage_size" {
  type    = number
  default = 20
}

variable "kafka_broker_count" {
  type    = number
  default = 3
}

variable "kafka_sasl_authen" {
  type    = bool
  default = true
}

variable "kafka_mtls_authen" {
  type    = bool
  default = false
}

variable "kafka_public_access" {
  type    = bool
  default = false
}

variable "kafka_config_properties" {
  type    = map(string)
  default = {}
}

variable "kafka_shared_topics" {
  type = list(object({
    name              = string
    partitions        = number
    replicas          = number
    retention_seconds = optional(number)
    retention_bytes   = optional(number)
  }))
  default = []
}

variable "kafka_per_tenant_topics" {
  type = list(object({
    name              = string
    partitions        = number
    replicas          = number
    retention_seconds = optional(number)
    retention_bytes   = optional(number)
  }))
  default = []
}

# ---------------------------------------------------------------------------
# vStorage (S3-compatible buckets via the AWS provider)
# ---------------------------------------------------------------------------
variable "vstorage_enabled" {
  type    = bool
  default = true
}

variable "vstorage_bucket_prefix" {
  type        = string
  description = "Prefix for globally-unique bucket names, e.g. \"c360-dev-\"."
  default     = ""
}

variable "vstorage_shared_buckets" {
  type    = list(string)
  default = []
}

variable "vstorage_per_tenant_buckets" {
  type    = list(string)
  default = []
}

variable "vstorage_versioning" {
  type    = bool
  default = false
}
