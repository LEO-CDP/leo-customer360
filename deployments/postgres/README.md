# PostgreSQL on GreenNode vDB (Terraform)

Provisions a managed PostgreSQL instance on **GreenNode vDB** (VNG Cloud's
managed relational-database service) using the official
[`vngcloud/vngcloud`](https://registry.terraform.io/providers/vngcloud/vngcloud/latest)
Terraform provider. This is the declarative equivalent of the console form at
`https://vdb.console.greennode.ai/relational/database/create/`.

## Files

| File | Purpose |
|------|---------|
| `provider.tf` | Provider + platform endpoints |
| `variables.tf` | All inputs and their defaults |
| `main.tf` | Package/volume lookups + the DB instance resource |
| `outputs.tf` | Instance id, name, resolved CPU/RAM |
| `overlays/uat.tfvars` | UAT env config (non-secret, committed) |
| `overlays/prod.tfvars` | PROD env config (same as UAT for now) |
| `terraform.tfvars.example` | Secrets template (copy to `terraform.tfvars`) |
| `deploy.sh` | Env-aware `<uat\|prod> plan/apply/destroy` wrapper |
| `.gitignore` | Keeps secrets and state out of git |

## Environments (overlays)

Each env is a Terraform **workspace** (isolated state) fed by its own
`overlays/<env>.tfvars`. Secrets are shared for now via the git-ignored
`terraform.tfvars` / `.env`. UAT is the current default; PROD mirrors it until it
diverges. `deploy.sh` selects the workspace and passes the right var-file.

## Prerequisites

- Terraform >= 1.3
- A **vIAM service account** (`client_id` / `client_secret`):
  GreenNode console → IAM → Service Account.
- The **subnet id** of the VPC the DB should live in (`sub-...`).
- The exact **package name**, **PostgreSQL version**, and **volume type** as
  shown in the console create form's dropdowns — copy them verbatim.

## Usage

```bash
cd deployments/postgres
cp terraform.tfvars.example terraform.tfvars   # fill in secrets (or use .env)
# edit overlays/uat.tfvars / overlays/prod.tfvars (subnet_id, sizing, ...)

./deploy.sh uat plan
./deploy.sh uat apply
./deploy.sh prod plan
```

On Windows, run `deploy.sh` from Git Bash. To drive Terraform directly, select the
workspace and pass the overlay yourself:
`terraform workspace select uat && terraform plan -var-file=overlays/uat.tfvars`.

## Notes

- **Endpoints:** defaults point at VNG Cloud's IAM/gateways, which GreenNode
  runs on (GreenNode's own API docs redirect to / authenticate against the same
  hosts). If your tenant was issued different gateway hostnames, override the
  `*_base_url` / `token_url` variables — grab the real hosts from the console
  via browser DevTools → Network.
- **High availability:** for a 1-writer + N-reader cluster, swap
  `vngcloud_vdb_relational_database` in `main.tf` for
  `vngcloud_vdb_postgresql_cluster` (same provider).
- **Secrets & state:** `terraform.tfvars` and `*.tfstate` hold the DB password
  in plaintext and are git-ignored. Use a remote backend for team use.
