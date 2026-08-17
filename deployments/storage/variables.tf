# ---------------------------------------------------------------------------
# Authentication — vStorage S3 keys (NOT the vIAM client_id/secret used by vDB).
# Create at: vStorage console -> IAM -> Service account -> vStorage credentials
# -> Create a S3 key. The Secret Key is shown ONLY ONCE — copy it then.
# ---------------------------------------------------------------------------
variable "access_key" {
  type        = string
  description = "vStorage S3 access key."
}

variable "secret_key" {
  type        = string
  description = "vStorage S3 secret key. Do NOT commit a real value."
  sensitive   = true
}

# ---------------------------------------------------------------------------
# Platform endpoint. Object storage for this account lives in HCM04 / HAN02
# (confirmed via the vStorage regions API) — NOT HCM03, which is a vServer/vDB
# zone only. Default to HCM04 (Ho Chi Minh). Copy the exact endpoint from the
# console if your project sits in a different region.
# ---------------------------------------------------------------------------
variable "s3_endpoint" {
  type        = string
  default     = "https://hcm04.vstorage.vngcloud.vn"
  description = "vStorage S3-compatible endpoint URL. Pattern: https://<region>.vstorage.vngcloud.vn. This account's object-storage regions: hcm04, han02."
}

variable "region" {
  type        = string
  default     = "hcm04"
  description = "Region string sent in the S3 signature. Match it to s3_endpoint's region; validation is skipped so it is a label only."
}

# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------
variable "bucket_names" {
  type        = list(string)
  description = "Buckets to create. Names must be globally unique within the vStorage tenant and DNS-safe (lowercase, 3-63 chars)."
  default     = ["leo-customer360"]

  validation {
    condition     = length(var.bucket_names) > 0
    error_message = "Provide at least one bucket name."
  }
  validation {
    condition = alltrue([
      for b in var.bucket_names : can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", b))
    ])
    error_message = "Each bucket name must be lowercase, 3-63 chars, and start/end with a letter or digit (S3 naming rules)."
  }
}

variable "enable_versioning" {
  type        = bool
  default     = false
  description = "If true, enable object versioning on every bucket (PutBucketVersioning). Off by default to keep a fresh apply minimal."
}

# ---------------------------------------------------------------------------
# Cost estimation (VND) — VNG Cloud vStorage rate card, per the quoted prices.
# These do NOT provision anything; they only drive the cost outputs so the
# monthly bill can be reviewed alongside the plan.
# ---------------------------------------------------------------------------
variable "price_storage_per_tb_vnd" {
  type        = number
  default     = 1000000
  description = "Price to store 1 TB of data per month (VND). Quoted: 1 TB = 1,000,000 VND."
}

variable "price_bandwidth_per_gb_vnd" {
  type        = number
  default     = 580
  description = "Price per 1 GB of bandwidth (VND). Quoted: 1 GB = 580 VND."
}

variable "estimated_storage_tb" {
  type        = number
  default     = 1
  description = "Expected stored data (TB) used for the monthly cost estimate."
}

variable "estimated_bandwidth_gb" {
  type        = number
  default     = 200
  description = "Expected bandwidth (GB) used for the monthly cost estimate. Quoted inbound + outbound total = 200 GB."
}
