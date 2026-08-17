# --- vngcloud provider auth ------------------------------------------------
variable "vng_token_url" {
  type    = string
  default = "https://iamapis.vngcloud.vn/accounts-api/v2/auth/token"
}
variable "vng_client_id" {
  type      = string
  sensitive = true
}
variable "vng_client_secret" {
  type      = string
  sensitive = true
}
variable "vng_vserver_base_url" {
  type    = string
  default = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
}
variable "vng_vlb_base_url" {
  type    = string
  default = "https://hcm-3.api.vngcloud.vn/vserver/vlb-gateway"
}
variable "vng_vdb_base_url" {
  type    = string
  default = "https://vdb-gateway.vngcloud.vn"
}

# --- vStorage (S3) provider auth -------------------------------------------
variable "vstorage_access_key" {
  type      = string
  sensitive = true
}
variable "vstorage_secret_key" {
  type      = string
  sensitive = true
}
variable "vstorage_region" {
  type    = string
  default = "hcm03"
}
variable "vstorage_s3_endpoint" {
  type    = string
  default = "https://hcm03.vstorage.vngcloud.vn"
}

# --- General ---------------------------------------------------------------
variable "name_prefix" {
  type    = string
  default = "c360"
}
variable "zone_id" {
  type    = string
  default = "HCM03-1A"
}
variable "vserver_project_id" {
  type = string
}

# --- Networking ------------------------------------------------------------
variable "create_network" {
  type    = bool
  default = false
}
variable "network_id" {
  type    = string
  default = ""
}
variable "subnet_id" {
  type    = string
  default = ""
}
variable "network_cidr" {
  type    = string
  default = "10.76.0.0/16"
}
variable "subnet_cidr" {
  type    = string
  default = "10.76.1.0/24"
}
variable "db_allowed_ip_prefixes" {
  type        = list(string)
  description = "Restrict to your VPC / app subnet CIDRs in prod. Do NOT use 0.0.0.0/0."
  default     = ["10.76.0.0/16"]
}

# --- Tenants ---------------------------------------------------------------
variable "tenants" {
  type = list(object({
    id   = string
    code = string
  }))
}

# --- PostgreSQL (cluster) --------------------------------------------------
variable "pg_engine_version" {
  type    = string
  default = "16"
}
variable "pg_package_name" {
  type        = string
  description = "Cluster package name, e.g. vdb.s-general-2x4."
  default     = "vdb.s-general-2x4"
}
variable "pg_volume_type" {
  type    = string
  default = "Gen2-NVMe-IOPS5000"
}
variable "pg_volume_size" {
  type    = number
  default = 50
}
variable "pg_cluster_nodes" {
  type    = number
  default = 3
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

# HA-cluster backups are configured via VNG Backup Center (vBackup), NOT via
# backup_auto/duration/time (those are standalone-only and inert for a cluster).
# Create a Backup Policy + Location in the console (or vBackup TF resources if
# available) and set their IDs here — else the prod cluster has NO automatic
# backups. Leave null only if you rely solely on manual snapshots + the logical
# pg_dump CronJob (postgres/docs/backup/backup-cronjob.yaml).
variable "pg_backup_policy_id" {
  type        = string
  description = "VNG Backup Center Backup Policy ID applied to the prod PG cluster."
  default     = null
}
variable "pg_backup_location_id" {
  type        = string
  description = "VNG Backup Center Backup Location ID for the prod PG cluster's backups."
  default     = null
}

# --- In-DB bootstrap (recommended: run via CI/K8s job, not from here) ------
variable "run_db_bootstrap" {
  type    = bool
  default = false
}
variable "app_role_password" {
  type      = string
  sensitive = true
  default   = ""
}
variable "db_bootstrap_extra_sql" {
  type    = list(string)
  default = []
}

# --- Redis -----------------------------------------------------------------
variable "redis_engine_version" {
  type    = string
  default = "7.2"
}
variable "redis_package_name" {
  type    = string
  default = "db.new.s-general-2x4"
}
variable "redis_password" {
  type      = string
  sensitive = true
}

# --- Kafka -----------------------------------------------------------------
variable "kafka_enabled" {
  type    = bool
  default = true
}
variable "kafka_package_name" {
  type    = string
  default = "db-kafka.s-general-2x4-n10"
}
variable "kafka_broker_count" {
  type    = number
  default = 3
}

# --- vStorage --------------------------------------------------------------
variable "vstorage_enabled" {
  type    = bool
  default = true
}
variable "vstorage_bucket_prefix" {
  type    = string
  default = "c360-prod-"
}
