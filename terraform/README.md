# Customer360 Infrastructure — Terraform (GreenNode / VNG Cloud)

Idempotent, multi-tenant provisioning of the four managed data services the
LEO Customer360 CDP depends on, on **GreenNode** (VNG Cloud's AI cloud):

| Service | Provider mechanism | Terraform resources |
|---|---|---|
| **PostgreSQL** | `vngcloud/vngcloud` (vDB) | `vngcloud_vdb_relational_database` (standalone) / `vngcloud_vdb_postgresql_cluster` (HA) + config groups |
| **Redis** | `vngcloud/vngcloud` (vDB Memory store) | `vngcloud_vdb_memstore_database` + config group |
| **Kafka** | `vngcloud/vngcloud` (vDB Kafka) | `vngcloud_vdb_kafka_cluster` + `_topic` + `_user` + config group |
| **vStorage (S3)** | `hashicorp/aws` against the vStorage **S3-compatible** endpoint | `aws_s3_bucket` (+ versioning) |

> vStorage has **no native vngcloud resource**. It is S3-compatible, so buckets
> are managed with the AWS provider pointed at `https://<region>.vstorage.vngcloud.vn`
> using an **S3 key** (Access/Secret) created for a vStorage Service Account —
> credentials that are separate from the vngcloud IAM client id/secret.

## Why this shape

Everything about the Customer360 codebase assumes **shared infrastructure with
in-data tenant isolation**:

- Postgres uses **Row-Level Security** on a `tenant_id` column (`database-init/database-schema.sql`).
- Events carry `tenant_id` in the payload; Redis is a single shared cache.

So the stack provisions **one shared cluster per service** and layers a **hybrid**
per-tenant edge on top (chosen for this project):

- **Postgres / Redis** — one shared instance; tenants isolated by RLS / app logic.
- **Kafka** — one shared cluster with **shared topics** (`tenant_id` in the message
  key) **plus** optional **per-tenant topics** and **per-tenant SASL users** scoped
  to only their own topics.
- **vStorage** — **shared** buckets (ingestion/exports/backups) **plus** optional
  **per-tenant** buckets (`<prefix><tenant_code>-<name>`).

Adding a tenant = add one entry to the `tenants` list and re-apply.

## Layout

```
terraform/
├── modules/
│   ├── network/        # optional VNG network + subnet
│   ├── postgres/       # standalone OR cluster (topology switch)
│   ├── redis/          # vDB Memory store
│   ├── kafka/          # cluster + shared/per-tenant topics + users
│   ├── vstorage/       # S3-compatible buckets (aws provider)
│   └── db-bootstrap/   # db_keycloak + extensions + RLS role + schema + seed via psql
├── stack/              # composition module wiring all of the above
└── environments/
    ├── dev/            # standalone PG, permissive CIDRs, no versioning
    └── prod/           # HA PG cluster, restricted CIDRs, versioned buckets
```

Each environment configures its own providers + backend and calls `../../stack`.

## Prerequisites

1. **Terraform** ≥ 1.5.
2. A **GreenNode / VNG Cloud** account with vServer project, vDB and vStorage enabled.
3. **vngcloud IAM** client id + secret (IAM console).
4. A **vStorage S3 key** (IAM console → Service Account → *Create S3 key* — save the
   secret immediately, it is shown only once).
5. An existing **network + subnet** (or set `create_network = true`).
6. For `db-bootstrap`: `psql` + `bash` on the runner and network reachability to the DB.

## Credentials to fill in

Put these in `environments/<env>/terraform.tfvars` (copied from `terraform.tfvars.example`,
gitignored) **or** export them as `TF_VAR_<name>` env vars. There are **two independent
credential sets** — the vngcloud IAM pair (for vDB + networking) and a separate vStorage
**S3 key** (for buckets) — plus the service passwords you choose.

| Variable | Required | What it is / where to get it |
|---|---|---|
| `vng_client_id` | ✅ | vngcloud **IAM** client id — GreenNode/VNG IAM console → API credentials. Drives all vDB (Postgres/Redis/Kafka) + network resources. |
| `vng_client_secret` | ✅ | Matching IAM client secret. |
| `vstorage_access_key` | ✅ (if `vstorage_enabled`) | vStorage **S3 access key** — IAM console → Service Account → *Create S3 key*. Separate from the IAM pair above. |
| `vstorage_secret_key` | ✅ (if `vstorage_enabled`) | Matching S3 secret key — **shown only once at creation**, save it immediately. |
| `vserver_project_id` | ✅ | vServer project id (`pro-…`) from the vServer console. Needed by Kafka and network creation. |
| `network_id` | ✅ unless `create_network=true` | Existing VNG network id (`net-…`). |
| `subnet_id` | ✅ unless `create_network=true` | Existing VNG subnet id (`sub-…`) the DBs attach to. |
| `pg_password` | ✅ | Master password you choose for PostgreSQL. |
| `redis_password` | ✅ | `requirepass` value you choose for Redis. |
| `app_role_password` | ✅ only if `run_db_bootstrap=true` | Password for the non-superuser `customer360_app` RLS role. |

Values with sensible **defaults you usually don't change**: `vng_token_url`,
`vng_vserver_base_url`, `vng_vlb_base_url`, `vng_vdb_base_url` (VNG HCM-3 gateways),
`vstorage_region` (`hcm03`), `vstorage_s3_endpoint` (`https://hcm03.vstorage.vngcloud.vn`).
Override only if your account is in a different region.

> Never commit real secrets. `terraform.tfvars` and `*.auto.tfvars` are gitignored; prefer
> `TF_VAR_*` env vars (or a secrets manager) in CI/prod. Terraform **state also contains these
> secrets**, so use a protected remote backend (see `providers.tf`).

## Usage

```bash
cd terraform/environments/dev          # or prod
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars (or export TF_VAR_* for secrets)

terraform init
terraform plan
terraform apply
terraform output                       # connection details for the app
```

Re-running `apply` with an unchanged config is a **no-op** (Terraform is
idempotent). To adopt resources created by hand in the console, use
`terraform import` (every vDB resource supports it; Kafka topics/users use the
`<cluster_id>/<id>` form).

## One-command deploy scripts

For a single-click deploy, use the scripts in this directory instead of the
manual steps above. They read credentials from `.env` (see *Credentials to fill
in*), then run `terraform init` + `terraform apply` for the chosen environment.
Run them from **Git Bash / WSL** (they are `.sh`):

```bash
cd terraform
./deploy-dev.sh     # standalone Postgres, dev sizing
./deploy-prod.sh    # HA Postgres cluster, prod sizing
```

Both are thin wrappers around `deploy.sh <dev|prod>`, which does:

```bash
set -a; source .env; set +a          # load TF_VAR_* credentials
cd environments/<env>
terraform init -input=false
terraform apply -auto-approve        # applies immediately - no prompt
```

> ⚠️ These apply **immediately with `-auto-approve`** and prod is treated the
> same as dev — there is no confirmation prompt or credential preflight. Make
> sure `.env` is filled in first (a placeholder `vng_client_id` fails fast at the
> vngcloud auth step). For a review-before-apply flow, use the manual
> `terraform plan` / `apply` steps above instead.

## Wiring outputs into the app

`terraform output` gives you the values for the app config (the k8s `c360-config`
ConfigMap / `.env`). These are the exact keys the current code reads:

```
DB_HOST                 <- postgres.host          DB_PORT  <- postgres.port
DB_NAME                 <- postgres.db_name        DB_USER  <- customer360_app (see RLS note below)
KC_DB_URL               <- jdbc:postgresql://<postgres.host>:<port>/db_keycloak   (Keycloak's dedicated DB)
REDIS_HOST              <- redis.host              REDIS_PORT <- redis.port  (managed port, NOT 6580)
KAFKA_BOOTSTRAP_SERVERS <- kafka.broker_private_ips joined with ":9094" (SASL) / ":9092" (plaintext)
S3_ENDPOINT             <- vstorage_s3_endpoint    (e.g. https://hcm03.vstorage.vngcloud.vn)
MINIO_BUCKET            <- one vstorage_buckets entry (the "events" bucket, e.g. c360-<env>-events)
```

> The app's S3 client authenticates with the **vStorage S3 key** (passed as
> `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` in the k8s `c360-secrets`), i.e. the same
> `vstorage_access_key`/`vstorage_secret_key` this stack uses — not the vngcloud
> IAM pair.

## The critical in-database step (RLS)

The vDB provider provisions the **server** but not what's **inside** the database.
Two things must run once against a fresh Postgres, and both are handled by the
`db-bootstrap` module (or your own migration job):

1. **Extensions** — `uuid-ossp`, `pgcrypto`, `vector` (pgvector), `postgis`,
   `pg_trgm`, `fuzzystrmatch` (exactly what `postgres/init/00-extensions.sql`
   creates). All are supported by vDB PostgreSQL and enabled idempotently.
2. **The `customer360_app` role** — a **non-superuser** login role. RLS is
   **silently bypassed** when the app connects as the `postgres` superuser, so the
   app MUST connect as this role. The repo documents it but never creates it —
   `db-bootstrap` does (idempotently). ⚠️ The current k8s `c360-config` still sets
   `DB_USER=postgres`, so RLS is inert in that config — point `DB_USER` at
   `customer360_app` (and set its password) to actually enforce tenant isolation.
3. **The `db_keycloak` database** — Keycloak shares this Postgres server but uses
   a **dedicated database** (`db_keycloak`), which the app targets via `KC_DB_URL`.
   Every non-Terraform path already creates it (docker-compose `keycloak-db-init`,
   the k8s Postgres image init). `db-bootstrap` now creates it too, idempotently,
   from `postgres/init/02-create-keycloak-db.sql` (toggle with `create_keycloak_db`).

`db-bootstrap` is `enabled = false` by default. To run it from Terraform, set
`pg_public_access = true`, `run_db_bootstrap = true` and `app_role_password` in
your tfvars. **Recommended for prod:** run the same idempotent SQL from a CI/CD
step or a Kubernetes Job instead of from Terraform.

## ⚠️ Verify before first apply

These values depend on your account/region and are best confirmed in the vDB
console (a wrong package/version name fails at `plan` on the data-source lookup):

- **Package names** — `pg_package_name`, `redis_package_name`, `kafka_package_name`
  (standalone uses `db.*`, cluster uses `vdb.*`, kafka uses `db-kafka.*`).
- **Engine versions** — `pg_engine_version` (repo runs 16), `redis_engine_version`
  (managed is 7.2.x; repo image is Redis 8 — functionally equivalent), `kafka_version`.
- **Volume type names** and **zone** (`HCM03-1A/-1B/-1C`).
- **vStorage endpoint / region** — confirm the host for your bucket's region and
  whether path-style vs virtual-hosted addressing is required (`s3_use_path_style`).
  This stack defaults to **`hcm03`** everywhere (zone `HCM03-1A`, vDB `HCM-3`
  gateways, `vstorage_region`/`vstorage_s3_endpoint`). The k8s `overlays/vks`
  `S3_ENDPOINT` example currently says `hcm04` — reconcile the two to the region
  your account actually uses so the app's `S3_ENDPOINT` matches these buckets.
- Kafka **standalone minimums** — brokers 3–10, `replicas` ≤ broker count.

## Cost note

`terraform apply` creates **billable** managed services (an HA PG cluster is 2–10
nodes; a Kafka cluster is ≥3 brokers). Start with `dev` (standalone PG) and mind
the `is_poc` flag if you are on PoC credit.
