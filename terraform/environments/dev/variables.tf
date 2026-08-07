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
  type    = list(string)
  default = ["0.0.0.0/0"] # dev only - restrict in prod
}

# --- Tenants ---------------------------------------------------------------
variable "tenants" {
  type = list(object({
    id   = string
    code = string
  }))
  default = [
    { id = "11111111-1111-1111-1111-111111111111", code = "default" },
  ]
}

# --- PostgreSQL ------------------------------------------------------------
variable "pg_engine_version" {
  type    = string
  default = "16"
}
variable "pg_package_name" {
  type        = string
  description = "Standalone package name, e.g. db.new.s-general-2x4."
  default     = "db.new.s-general-2x4"
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
  description = "Expose Postgres publicly (required if run_db_bootstrap runs from your laptop)."
  default     = false
}

# --- In-DB bootstrap -------------------------------------------------------
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
  default = "db.new.s-general-1x2"
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

# --- vStorage --------------------------------------------------------------
variable "vstorage_enabled" {
  type    = bool
  default = true
}
variable "vstorage_bucket_prefix" {
  type        = string
  description = "Globally-unique prefix, e.g. c360-dev-."
  default     = "c360-dev-"
}
