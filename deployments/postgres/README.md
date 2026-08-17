# PostgreSQL on GreenNode vDB (Terraform)

Provisions a managed PostgreSQL instance (optionally its VPC + subnet) on
**GreenNode vDB** (VNG Cloud's managed relational-database service) using the official
[`vngcloud/vngcloud`](https://registry.terraform.io/providers/vngcloud/vngcloud/latest)
Terraform provider, with per-environment overlays and an env-aware wrapper script.
This is the declarative equivalent of the console form at
`https://vdb.console.greennode.ai/relational/database/create/`.

## Files

| File | Purpose |
|------|---------|
| `provider.tf` | Provider (pinned `~> 1.3.19`) + platform endpoints |
| `variables.tf` | All inputs, defaults, and validations |
| `main.tf` | Optional VPC+subnet, package/volume lookups, the DB resource |
| `outputs.tf` | Instance id, name, resolved CPU/RAM |
| `overlays/uat.tfvars` | UAT env config (non-secret, committed) |
| `overlays/prod.tfvars` | PROD env config (same as UAT for now) |
| `terraform.tfvars.example` | Secrets template (copy to `terraform.tfvars`) |
| `.env.example` | Alternative secrets via `TF_VAR_*` (copy to `.env`) |
| `deploy.sh` | Env-aware `<uat\|prod> plan/apply/destroy` wrapper |
| `../issues/` | Known-issue write-ups (e.g. the 10000-IOPS zone blocker) |
| `.gitignore` | Keeps secrets, state, and caches out of git |

## Environments (overlays)

Each env is a Terraform **workspace** (isolated state under `terraform.tfstate.d/<env>/`)
fed by its own `overlays/<env>.tfvars` (non-secret, committed). Secrets are shared for now
via the git-ignored `terraform.tfvars` / `.env`. `deploy.sh` selects the workspace and
passes the right var-file. Precedence: the `-var-file` overlay overrides the auto-loaded
`terraform.tfvars`, so config and secrets never collide.

## Prerequisites

- Terraform >= 1.3.
- A **vIAM service account** (`client_id` / `client_secret`): GreenNode console → IAM →
  Service Account (the Secret is shown once).
- Your **project id** (`pro-...`) — required to create the VPC/subnet. Console project
  selector/overview, or the API: `GET vserver-gateway/v1/{project}/zones` returns it.
- If NOT creating the network (`create_network = false`): an existing **subnet id**
  (`sub-...`) in the target zone.
- The exact **package name**, **PostgreSQL version**, **volume type**, and an **enabled
  zone** — these are per-zone and account-specific (see the reality check below).

## Usage

```bash
cd deployments/postgres
cp terraform.tfvars.example terraform.tfvars   # fill in client_id/secret + db_password
#   (or: cp .env.example .env  and use TF_VAR_* instead)

# edit overlays/uat.tfvars (project_id, zone, package, volume, network CIDRs)
./deploy.sh uat plan          # review
./deploy.sh uat apply         # create VPC + subnet + DB
# …repeat with prod
```

`apply` is idempotent — it plans first and is a no-op when nothing changed. On Windows run
`deploy.sh` from Git Bash. To drive Terraform directly:
`terraform workspace select uat && terraform plan -var-file=overlays/uat.tfvars`.

## Network creation (optional)

Set `create_network = true` (both overlays do) to have Terraform create a VPC
(`vngcloud_vserver_network`) + subnet (`vngcloud_vserver_subnet`) and attach the DB to it;
this needs `project_id`, `network_cidr` (/16), and `subnet_cidr`. Set `create_network =
false` and provide `subnet_id` to use an existing subnet instead. Both network resources
require `project_id` (the vDB resource itself infers project from the token).

## Reality check for THIS account (important)

The catalog and zone availability are **account- and zone-specific**, and they bit us:

- Only **`HCM03-1C`** is enabled for vServer/subnets (1A/1B are disabled — "contact to
  enable"). So the subnet — and therefore the DB — must live in `HCM03-1C`.
- **Catalog names differ per zone.** Current working values (HCM03-1C standalone):
  `package_name = "db.s-general-8x16"`, `volume_type = "ssd-iops3200-HCM03-1C"`.
  (HCM03-1A used `db.s2-general-8x16` + `Gen2-NVMe2-IOPS10000`.)
- **10000 IOPS is not available for standalone in HCM03-1C** (it maxes at 3200). Reaching
  10000 IOPS needs either enabling `HCM03-1A` (support ticket) or the cluster topology.
  Full write-up + options + action items:
  [`../issues/2026-08-17-vdb-postgres-10000-iops-blocked.md`](../issues/2026-08-17-vdb-postgres-10000-iops-blocked.md).

If a package/volume name doesn't match, a `precondition` in `main.tf` fails the plan with an
actionable message (the provider otherwise returns an empty id → a confusing "Missing
required argument").

## Validations (fail fast at plan)

- `instance_name`: 6–20 characters (vDB limit).
- `db_password`: starts with a letter, doesn't start/end with a special char, ≥ 8 chars.
- `zone_id`: one of `HCM03-1A|1B|1C`.

## Notes

- **Endpoints:** defaults point at VNG Cloud's IAM/gateways, which GreenNode runs on. Override
  `*_base_url` / `token_url` if your tenant uses different hosts.
- **High availability / 10000 IOPS:** swap `vngcloud_vdb_relational_database` for
  `vngcloud_vdb_postgresql_cluster` (same provider). Note: the cluster resource does NOT accept
  `backup_auto`/`backup_duration`/`backup_time` — cluster backups go via VNG Backup Center
  (`backup_policy_id` / `backup_location_id`).
- **Secrets & state:** `terraform.tfvars`, `.env`, and `*.tfstate` hold secrets in plaintext
  and are git-ignored. Use a shared remote backend + locking for team/CI use (local state is
  what makes re-runs idempotent — a second machine with no state would create duplicates).
