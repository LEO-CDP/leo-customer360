# UAT overlay — environment-specific, NON-SECRET config.
# Secrets (access_key, secret_key) live in ../terraform.tfvars or ../.env.
# Apply with:  ./deploy.sh uat <plan|apply>   (Terraform workspace "uat").
#
# PREREQUISITE: the vStorage PROJECT (with its quota/package) must already exist
# in the console, and the S3 key in terraform.tfvars must belong to it. Terraform
# only manages buckets INSIDE that project — it cannot create the project. See README.

s3_endpoint = "https://hcm04.vstorage.vngcloud.vn" # object storage = HCM04/HAN02 (NOT HCM03)
region      = "us-east-1"                          # keep us-east-1 (no LocationConstraint) — see variables.tf

bucket_names = ["leo-customer360-uat"]

enable_versioning = false

# --- Cost estimate (VND / month) — tune to the env's expected usage ---
estimated_storage_tb       = 1
estimated_bandwidth_gb     = 200
price_storage_per_tb_vnd   = 1000000
price_bandwidth_per_gb_vnd = 580
