variable "topology" {
  type        = string
  description = "standalone (single node) or cluster (HA, 2-10 nodes)."
  validation {
    condition     = contains(["standalone", "cluster"], var.topology)
    error_message = "topology must be either \"standalone\" or \"cluster\"."
  }
}

variable "name_prefix" {
  type        = string
  description = "Prefix applied to PostgreSQL resource names."
}

variable "zone_id" {
  type        = string
  description = "Availability zone (e.g. HCM03-1A)."
}

variable "subnet_id" {
  type        = string
  description = "Subnet the instance/cluster is attached to."
}

variable "engine_version" {
  type        = string
  description = "PostgreSQL major version (e.g. 16 or 17). Repo runs 16; confirm availability in vDB."
  default     = "16"
}

variable "package_name" {
  type        = string
  description = "vDB package name. Standalone uses db.* names (e.g. db.new.s-general-2x4); cluster uses vdb.* names (e.g. vdb.s-general-2x4)."
}

variable "volume_type" {
  type        = string
  description = "Volume type name, e.g. Gen2-NVMe-IOPS5000 (cluster) or Gen2-NVMe2-IOPS5000 (standalone)."
  default     = "Gen2-NVMe-IOPS5000"
}

variable "volume_size" {
  type        = number
  description = "Data volume size in GB."
  default     = 20
}

variable "cluster_nodes" {
  type        = number
  description = "Number of nodes when topology = cluster (2-10). Ignored for standalone."
  default     = 3
}

variable "username" {
  type        = string
  description = "Master username."
  default     = "postgres"
}

variable "password" {
  type        = string
  description = "Master password."
  sensitive   = true
}

variable "db_name" {
  type        = string
  description = "Initial database name."
  default     = "customer360"
}

variable "public_access" {
  type        = bool
  description = "Expose the instance/cluster with a public IP."
  default     = false
}

variable "allowed_ip_prefixes" {
  type        = list(string)
  description = "CIDRs allowed to reach PostgreSQL. Restrict this in prod."
  default     = ["0.0.0.0/0"]
}

variable "config_values" {
  type        = map(string)
  description = "Config group parameters (e.g. max_connections, autovacuum). Empty = no config group."
  default     = {}
}

variable "backup_auto" {
  type        = bool
  description = "Standalone only: enable daily automatic backups. Ignored for topology=cluster (use backup_policy_id/backup_location_id)."
  default     = true
}

# --- Cluster backups (vBackup / Backup Center) -------------------------------
# The vngcloud_vdb_postgresql_cluster resource does NOT accept backup_auto/
# backup_duration/backup_time. HA-cluster backups are configured by attaching a
# Backup Policy + Backup Location created in VNG Backup Center (vBackup). Both
# are Optional+Computed on the resource: setting them applies the policy at
# create time; leaving them null keeps whatever the console/vBackup assigned.
variable "backup_policy_id" {
  type        = string
  description = "Cluster only: Backup Policy ID (schedule + retention) from VNG Backup Center. null = leave as-is."
  default     = null
}

variable "backup_location_id" {
  type        = string
  description = "Cluster only: Backup Location ID (where backups are stored) from VNG Backup Center. null = leave as-is."
  default     = null
}

variable "backup_duration" {
  type        = number
  description = "Standalone only: backup retention in days (2-14)."
  default     = 7
}

variable "backup_time" {
  type        = string
  description = "Standalone only: daily backup time."
  default     = "01:00"
}
