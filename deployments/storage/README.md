# Object Storage on VNG Cloud vStorage (Terraform)

Provisions **S3-compatible object storage buckets** on **VNG Cloud vStorage**
and prints a monthly cost estimate from the quoted rate card. Mirrors the layout
of [`deployments/postgres`](../postgres): per-env overlays, a workspace-isolated
`deploy.sh`, git-ignored secrets.

## Why the AWS provider (not `vngcloud/vngcloud`)

vStorage is **S3-compatible**, and the native `vngcloud/vngcloud` provider does
**not** expose object storage (it only covers vServer, vDB, vLB, vKS). So buckets
are managed with the standard `hashicorp/aws` provider pointed at the vStorage S3
endpoint, with every AWS-only preflight (`STS`, `IMDS`, account-id, region
allow-list) switched off and path-style addressing on. See `provider.tf`.

## Files

| File | Purpose |
|------|---------|
| `provider.tf` | AWS provider aimed at the vStorage S3 endpoint (skip_* flags) |
| `variables.tf` | All inputs and their defaults |
| `main.tf` | The bucket(s) + optional versioning |
| `cost.tf` | Monthly cost estimate arithmetic (creates nothing) |
| `outputs.tf` | Bucket names/URLs + cost estimate outputs |
| `overlays/uat.tfvars` | UAT env config (non-secret, committed) |
| `overlays/prod.tfvars` | PROD env config |
| `terraform.tfvars.example` | Secrets template (copy to `terraform.tfvars`) |
| `.env.example` | Alternative secrets via `TF_VAR_*` (copy to `.env`) |
| `deploy.sh` | Env-aware `<uat\|prod> plan/apply/destroy` wrapper |
| `undeploy.sh` | Tear down an env's buckets (destroy) with preview + confirm; `--force` empties non-empty buckets |
| `.gitignore` | Keeps secrets and state out of git |

## Pricing (quoted rate card)

| Item | Rate | Default estimate |
|------|------|------------------|
| Storage | 1 TB = **1,000,000 VND** / month | `estimated_storage_tb = 1` |
| Bandwidth | 1 GB = **580 VND** | `estimated_bandwidth_gb = 200` (inbound + outbound total) |

Default estimate → `1 * 1,000,000 + 200 * 580 = 1,116,000 VND/month`. `terraform
plan` / `terraform output estimated_cost_summary` prints the breakdown. These
inputs only drive the outputs — they provision nothing. The **storage quota is
set on the vStorage project** (see below), not by Terraform; these `estimated_*`
values are just a forecast to review the monthly bill.

## Hierarchy — the vStorage project comes first

```
vStorage PROJECT   ← created in the console: region + quota/package + billing period
   └── S3 key       ← created inside the project; scopes access_key/secret_key to it
        └── bucket(s)  ← what THIS Terraform manages, over the S3 API
```

Terraform **cannot** create the project: the `vngcloud/vngcloud` provider does
not expose vStorage, and project creation is a billing action (pick the storage
quota, package, period, auto-renew, then check out) that is not part of the S3
API the `aws` provider speaks. So the project is a **manual prerequisite** — like
the vIAM service account the postgres module needs. The S3 key you configure is
what binds this module to a specific project; Terraform then just manages buckets
inside it.

## Prerequisites

- Terraform >= 1.3
- **A vStorage project** — create it first at `https://vstorage.console.vngcloud.vn`
  → select region → **Create a Project** → choose quota/package + period. This is
  where the storage capacity (and its cost) is provisioned.
- **vStorage S3 keys** (`access_key` / `secret_key`), created **inside that
  project**: vStorage console → IAM → Service account → vStorage credentials →
  **Create a S3 key** (Secret shown once). The key ties Terraform to the project.
- The exact **S3 endpoint** for the project's region — pattern
  `https://<region>.vstorage.vngcloud.vn`. This account's object-storage regions
  are **`hcm04`** (default here) and **`han02`** — **not** `hcm03` (that's a
  vServer/vDB zone only). Copy the exact endpoint from the console if unsure.

> **Project creation is console-only for now.** Creating a vStorage project via
> the REST API failed at the billing/order step for this account (`code 114`,
> "Could not send order request") for every payload, so the API bootstrap was
> dropped. Create the project in the console — the full investigation, the API
> facts (endpoint, auth, body schema, 30 GB quota min), and the reason are in
> [`../issues/2026-08-17-vstorage-create-project-api-blocked.md`](../issues/2026-08-17-vstorage-create-project-api-blocked.md).

## Usage

```bash
cd deployments/storage
cp terraform.tfvars.example terraform.tfvars   # fill in the S3 keys (or use .env)
# edit overlays/uat.tfvars / overlays/prod.tfvars (bucket_names, endpoint, sizing)

./deploy.sh uat plan
./deploy.sh uat apply
./deploy.sh prod plan

# Tear down (destroy) an env's buckets — previews, then asks you to type the env name:
./undeploy.sh uat            # fails if a bucket still has objects
./undeploy.sh uat --force    # empties non-empty buckets first (deletes ALL objects)
```

On Windows, run `deploy.sh` from Git Bash. To drive Terraform directly, select
the workspace and pass the overlay yourself:
`terraform workspace select uat && terraform plan -var-file=overlays/uat.tfvars`.

## Notes

- **Bucket names are globally unique** within the vStorage tenant, so UAT and
  PROD must use different names (the overlays already do).
- **Endpoint:** `s3_endpoint` defaults to **HCM04** (this account's object-storage
  regions are `hcm04` / `han02`, not `hcm03`). Override it in the overlay if your
  project lives elsewhere. `region` is a SigV4 signing label only and must stay
  **`us-east-1`** so the AWS SDK omits the bucket `LocationConstraint` (vStorage
  rejects any other value with `InvalidLocationConstraint`).
- **Teardown:** `./undeploy.sh <env>` destroys the buckets (with a preview + typed
  confirmation) and leaves the vStorage project alone. S3 won't delete a non-empty
  bucket, so add `--force` to empty it first (deletes all objects, irreversible).
- **Versioning** is off in UAT, on in PROD (`enable_versioning`). Only the
  versioning API is used beyond bucket create; AWS-only sub-resources
  (public-access-block, ownership controls) are omitted as vStorage lacks them.
- **Secrets & state:** `terraform.tfvars`, `.env`, and `*.tfstate` hold the S3
  secret key in plaintext and are git-ignored. Use a remote backend for team use.
