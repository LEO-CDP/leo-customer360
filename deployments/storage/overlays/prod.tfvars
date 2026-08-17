# PROD overlay — SAME shape as UAT (bump usage/versioning when prod diverges).
# Secrets (access_key, secret_key) live in ../terraform.tfvars or ../.env.
#
# PREREQUISITE: the vStorage PROJECT (with its quota/package) must already exist
# in the console, and the S3 key in terraform.tfvars must belong to it. Terraform
# only manages buckets INSIDE that project — it cannot create the project. See README.
#
# NOTE: bucket names are GLOBALLY unique in the tenant, so prod's names must
# differ from uat's or the apply collides.

s3_endpoint = "https://hcm04.vstorage.vngcloud.vn" # object storage = HCM04/HAN02 (NOT HCM03)
region      = "hcm04"

bucket_names = ["leo-customer360-prod"]

enable_versioning = true

# --- Cost estimate (VND / month) — tune to the env's expected usage ---
estimated_storage_tb       = 1
estimated_bandwidth_gb     = 200
price_storage_per_tb_vnd   = 1000000
price_bandwidth_per_gb_vnd = 580
