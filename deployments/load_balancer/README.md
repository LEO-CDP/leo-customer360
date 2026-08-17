# Load Balancer (NLB) on GreenNode vLB (Terraform)

Provisions a managed **Network Load Balancer** (package **`NLB_Small`**,
type `Layer 4`) on **GreenNode vLB** (VNG Cloud's managed load-balancer
service) using the official
[`vngcloud/vngcloud`](https://registry.terraform.io/providers/vngcloud/vngcloud/latest)
Terraform provider. This is the declarative equivalent of the console
"Create Load Balancer" form.

Structure mirrors [`deployments/postgres`](../postgres): same provider/endpoints,
same per-env overlay + workspace model, same `deploy.sh` wrapper.

## Files

| File | Purpose |
|------|---------|
| `provider.tf` | Provider + platform endpoints |
| `variables.tf` | All inputs and their defaults |
| `main.tf` | Package lookup + optional network + the LB resource |
| `outputs.tf` | LB id, name, address, status, resolved package_id |
| `overlays/uat.tfvars` | UAT env config (non-secret, committed) |
| `overlays/prod.tfvars` | PROD env config (same as UAT for now) |
| `terraform.tfvars.example` | Secrets template (copy to `terraform.tfvars`) |
| `deploy.sh` | Env-aware `<uat\|prod> plan/apply/destroy` wrapper |
| `.gitignore` | Keeps secrets and state out of git |

## What it creates

`vngcloud_vlb_load_balancer` with:

- `package_id` — resolved at plan time from `package_name` (default `NLB_Small`)
  via the `vngcloud_vlb_lb_packages` data source. The data source lists **every**
  package in the project; the config matches yours by `name` and passes the
  package's `uuid` to the LB.
- `type = "Layer 4"` — Network Load Balancer. Use `"Layer 7"` for an ALB.
- `scheme = "Internet"` — public IP. Use `"Internal"` for a private-only LB.
- `subnet_id` — the subnet the LB lives in (see Network below).

Exposed via outputs: the LB `id`, `address` (its IP), `status`, and — for
discovery — `lb_packages`, the full package list so you can copy an exact
`package_name`.

## Environments (overlays)

Each env is a Terraform **workspace** (isolated state) fed by its own
`overlays/<env>.tfvars`. Secrets are shared for now via the git-ignored
`terraform.tfvars` / `.env`. UAT is the current default; PROD mirrors it until it
diverges. `deploy.sh` selects the workspace and passes the right var-file.

## Network

The LB needs a `subnet_id`. Normally set **`create_network = false`** and point
`subnet_id` at the **existing** subnet where the LB's backends run — an isolated
VPC could not route to them. The overlays ship with a placeholder `sub-...` you
must replace (e.g. with the subnet `deployments/postgres` created for that env).
Setting `create_network = true` will instead provision a fresh VPC + subnet
(rarely what you want for an LB).

## Prerequisites

- Terraform >= 1.3
- A **vIAM service account** (`client_id` / `client_secret`):
  GreenNode console → IAM → Service Account (attach a vLB/vServer policy).
- Your **project id** (`pro-...`) and the **subnet id** (`sub-...`) the LB
  should live in.
- The exact **package name** as shown in the console dropdown (e.g. `NLB_Small`).

## Usage

```bash
cd deployments/load_balancer
cp terraform.tfvars.example terraform.tfvars   # fill in secrets (or use .env)
# edit overlays/uat.tfvars / overlays/prod.tfvars (subnet_id, project_id, sizing)

./deploy.sh uat plan
./deploy.sh uat apply
./deploy.sh prod plan
```

On Windows, run `deploy.sh` from Git Bash. To drive Terraform directly, select the
workspace and pass the overlay yourself:
`terraform workspace select uat && terraform plan -var-file=overlays/uat.tfvars`.

Not sure of the exact package name? Run a `plan`/`apply` and read the
`lb_packages` output, or temporarily set `package_name` to a known value and
inspect the list — each entry shows `name`, `uuid`, and `lb_type`
(`L4` = NLB, `L7` = ALB).

## Notes

- **Endpoints:** defaults point at VNG Cloud's IAM/gateways, which GreenNode
  runs on. If your tenant was issued different gateway hostnames, override the
  `*_base_url` / `token_url` variables.
- **Listeners & pools:** this config creates the LB itself. To attach traffic
  rules, add `vngcloud_vlb_listener` + `vngcloud_vlb_pool` resources (same
  provider) referencing `vngcloud_vlb_load_balancer.this.id`.
- **State:** `terraform.tfvars` and `*.tfstate` are git-ignored. Use a remote
  backend for team use.
