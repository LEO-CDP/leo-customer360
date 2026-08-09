variable "enabled" {
  type        = bool
  description = "Run the in-database bootstrap. Requires psql/bash and DB reachability."
  default     = false
}

variable "instance_id" {
  type        = string
  description = "PostgreSQL instance/cluster ID (used as a re-run trigger and to order after provisioning)."
}

variable "host" {
  type        = string
  description = "DB host to connect to (usually the public host, since Terraform runs outside the VPC)."
}

variable "port" {
  type        = number
  description = "DB port."
  default     = 5432
}

variable "master_user" {
  type        = string
  description = "Master/superuser username used to run the bootstrap."
  default     = "postgres"
}

variable "master_password" {
  type        = string
  description = "Master/superuser password."
  sensitive   = true
}

variable "db_name" {
  type        = string
  description = "Target database name."
  default     = "customer360"
}

variable "db_schema" {
  type        = string
  description = "Application schema that RLS grants target."
  default     = "customer360"
}

variable "create_keycloak_db" {
  type        = bool
  description = "Also create the dedicated Keycloak database on the same instance. The app wires KC_DB_URL (k8s c360-config) to this DB, so every non-TF path (docker-compose keycloak-db-init, k8s) already creates it. Keep on so a TF-provisioned Postgres is usable by Keycloak too."
  default     = true
}

variable "keycloak_db_sql" {
  type        = string
  description = "Path to the idempotent CREATE DATABASE script for Keycloak (postgres/init/02-create-keycloak-db.sql). Only used when create_keycloak_db = true."
  default     = ""
}

variable "app_role_name" {
  type        = string
  description = "Non-superuser role the app connects as (RLS depends on this)."
  default     = "customer360_app"
}

variable "app_role_password" {
  type        = string
  description = "Password for the application role. Use a strong value without single quotes/backslashes."
  sensitive   = true
}

variable "extensions_sql" {
  type        = string
  description = "Path to the CREATE EXTENSION script (postgres/init/00-extensions.sql)."
}

variable "schema_sql" {
  type        = string
  description = "Path to the schema DDL (database-init/database-schema.sql)."
}

variable "seed_sql" {
  type        = string
  description = "Path to the seed data script (database-init/init-core-database.sql)."
}

variable "extra_sql" {
  type        = list(string)
  description = "Additional SQL files to apply last (e.g. database-init/data-view-for-llm.sql)."
  default     = []
}
