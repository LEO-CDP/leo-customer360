# API Servers on GreenNode vServer (Terraform)

Provisions the **API Server** compute instances (Ubuntu Server 24.04 VMs), and
optionally their VPC + subnet, on **GreenNode vServer** (VNG Cloud's compute
service) using the official
[`vngcloud/vngcloud`](https://registry.terraform.io/providers/vngcloud/vngcloud/latest)
Terraform provider, with per-environment overlays and an env-aware wrapper script.
This is the declarative equivalent of the console "Create instance" form.

Built to the request spec — both API-server sizes are created in **every**
environment:

| Server (map key) | Flavor          | vCPU | RAM   | Root disk | OS                |
|------------------|-----------------|------|-------|-----------|-------------------|
| `4x8`            | `s2-general-4x8`  | 4    | 8 GB  | 50 GB SSD | Ubuntu Server 24.04 |
| `8x16`           | `s2-general-8x16` | 8    | 16 GB | 50 GB SSD | Ubuntu Server 24.04 |

## Files

| File | Purpose |
|------|---------|
| `provider.tf` | Provider (pinned `~> 1.3.19`) + platform endpoints |
| `variables.tf` | All inputs, defaults, and validations |
| `main.tf` | Optional VPC+subnet, catalog lookups, optional SSH key, the servers |
| `outputs.tf` | Per-server ids/IPs, resolved flavor CPU/RAM, image & disk-type ids |
| `overlays/uat.tfvars` | UAT env config (non-secret, committed) |
| `overlays/prod.tfvars` | PROD env config (same shape as UAT for now) |
| `terraform.tfvars.example` | Secrets template (copy to `terraform.tfvars`) |
| `.env.example` | Alternative secrets via `TF_VAR_*` (copy to `.env`) |
| `deploy.sh` | Env-aware `<uat\|prod> plan/apply/destroy` wrapper |
| `../issues/` | Known-issue write-ups (e.g. the zone-availability blocker) |
| `.gitignore` | Keeps secrets, state, and caches out of git |

## How the catalog resolves (name → id)

The `vngcloud_vserver_server` resource wants opaque ids (`flavor_id`, `image_id`,
`root_disk_type_id`, `network_id`, `subnet_id`). This config resolves them from
human names via data sources, and — importantly — the flavor/image/volume-type
lookups each need a **zone UUID**, which is itself looked up from a display name:

```
flavor_zone_name  ─► data.vngcloud_vserver_flavor_zone.id ─┬─► vserver_flavor(name)      ─► flavor_id
                                                           └─► vserver_image(name)       ─► image_id
volume_type_zone_name ─► data.vngcloud_vserver_volume_type_zone.id ─► vserver_volume_type(name) ─► root_disk_type_id
```

## Environments (overlays)

Each env is a Terraform **workspace** (isolated state under `terraform.tfstate.d/<env>/`)
fed by its own `overlays/<env>.tfvars` (non-secret, committed). Secrets are shared for now
via the git-ignored `terraform.tfvars` / `.env`. `deploy.sh` selects the workspace and
passes the right var-file. Precedence: the `-var-file` overlay overrides the auto-loaded
`terraform.tfvars`, so config and secrets never collide.

## Prerequisites

- Terraform >= 1.3.
- A **vIAM service account** (`client_id` / `client_secret`): GreenNode console → IAM →
  Service Account, with a vServer policy (the Secret is shown once).
- Your **project id** (`pro-...`) — console project selector/overview.
- The exact **flavor-family name**, **image name**, **volume-type** names, and an
  **enabled zone** — these are per-zone/account-specific (see the reality check below).
- If NOT creating the network (`create_network = false`): existing **network id**
  (`net-...`) AND **subnet id** (`sub-...`) in the target zone.

## Usage

```bash
cd deployments/server
cp terraform.tfvars.example terraform.tfvars   # fill in client_id/secret + ssh_public_key
#   (or: cp .env.example .env  and use TF_VAR_* instead)

# edit overlays/uat.tfvars (project_id, zone, catalog names, network CIDRs)
./deploy.sh uat plan          # review
./deploy.sh uat apply         # create VPC + subnet + SSH key + both servers
# …repeat with prod
```

`apply` is idempotent — it plans first and is a no-op when nothing changed. On Windows run
`deploy.sh` from Git Bash. To drive Terraform directly:
`terraform workspace select uat && terraform plan -var-file=overlays/uat.tfvars`.

## Adjusting which servers get built

`var.servers` is a map of `name-suffix → { flavor_name, root_disk_size }`; the resource
`for_each`es over it. Both overlays default to **both** sizes. To build only one in an env,
drop the other key from that overlay's `servers`. To add a third, add a key.

## Network creation (optional)

Set `create_network = true` (both overlays do) to have Terraform create a VPC
(`vngcloud_vserver_network`) + subnet (`vngcloud_vserver_subnet`) and place the servers in
it; this needs `project_id`, `network_cidr` (/16), and `subnet_cidr`. Set `create_network =
false` and provide BOTH `network_id` and `subnet_id` to use an existing network instead.

## Reality check for THIS account (important)

The catalog and zone availability are **account- and zone-specific**. The sibling
`postgres` deployment already hit this (only `HCM03-1C` was enabled for vServer/subnets;
1A/1B were "contact to enable", and catalog names differed per zone). So before the first
apply, **copy the exact strings from the console create form** into the overlay:

- `flavor_zone_name` — the compute-family group label the `s2-general` flavors sit under
  (defaulted to `"General v2 Instances"` — VERIFY).
- `image_name` — the exact Ubuntu Server 24.04 entry (defaulted to `"Ubuntu 24.04 x64"` — VERIFY).
- `volume_type_zone_name` / `root_disk_type_name` — the SSD group + type (defaulted to
  `"SSD"` / `"SSD-IOPS3000"` — VERIFY).
- `zone_id` — an **enabled** AZ for this account.

If any name doesn't match, a `precondition` in `main.tf` fails the plan with an actionable
message (the data source otherwise returns an empty id → a confusing "Missing required
argument"). See [`../issues/`](../issues/) for prior zone/catalog blockers.

## Validations (fail fast at plan)

- `zone_id`: one of `HCM03-1A|1B|1C`.
- `action`: one of `start|stop|reboot`.
- `create_ssh_key = true` requires `ssh_public_key`.
- network: `create_network = true`, or BOTH `network_id` and `subnet_id` set.

## Login

Both overlays set `create_ssh_key = true` and a `ssh_key_name`; supply the key material via
`ssh_public_key` (secret). The same key is attached to both servers. Alternatively set
`user_name` + `user_password` for password login, or attach an existing key by setting
`create_ssh_key = false` and `ssh_key_name` to the existing key's name/id. `user_data` takes
a cloud-init script for first-boot bootstrap (e.g. install Docker / your API runtime).

## Notes

- **Endpoints:** defaults point at VNG Cloud's IAM/gateways, which GreenNode runs on. Override
  `*_base_url` / `token_url` if your tenant uses different hosts.
- **`ssh_key` field:** the provider example passes the SSH key **id**; this config passes the
  created key's `.id`. If your provider build expects the key **name** instead, switch
  `local.ssh_key` in `main.tf` to `vngcloud_vserver_sshkey.this[0].name`.
- **Secrets & state:** `terraform.tfvars`, `.env`, and `*.tfstate` hold secrets in plaintext
  and are git-ignored. Use a shared remote backend + locking for team/CI use (local state is
  what makes re-runs idempotent — a second machine with no state would create duplicates).
