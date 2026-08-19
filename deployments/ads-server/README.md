# deployments/ads-server — LEO Ad Server

Deploys `ads-server` (FastAPI/uvicorn, port **9009**) — the ad decisioning API. It
reads/writes its own **`leo_ads`** schema in the shared `customer360` database,
uses the shared **Redis** response cache, and exposes `/health`.

| Env | Where | Why |
|-----|-------|-----|
| `uat`  | container on the **api box** (`c360-api-uat-api`, server key `api`) | light service + reuses local Redis `127.0.0.1:6580`; the box has headroom |
| `prod` | container on a **dedicated vServer** (server key `ads`) | ad-serving is high-QPS — don't share the api box's single vCPU under load |

No secrets live here: DB creds come from `../postgres`, the Redis password from `../cache`.

## Deploy

```bash
./deploy-ads.sh uat            # build + run on the api box :9009, ensure leo_ads schema
./deploy-ads.sh uat destroy    # remove the container
```

`deploy-ads.sh` discovers the target VM from `../server` (by `ads_server_key`), ships
`ads-server/`, **bootstraps the `leo_ads` schema** (`sql-scripts/db-schema-init.sql`,
idempotent) over psql on the box, optionally loads sample data
(`sql-scripts/sample-data-init.sql` when `ads_seed_sample=true`), builds the image
(stripping the BuildKit `--mount`), and runs it `--network host` with an env file
(shipped base64) carrying `DB_*` (schema `leo_ads`) + `REDIS_*`.

`leo_ads` has **no RLS**, so the bootstrap runs cleanly as `app_admin` — unlike the
`customer360` schema, no `app.tenant_id` / `PGOPTIONS` is needed.

## Expose via the load balancer

`deployments/load_balancer` has an `ads` backend: LB `:9009 → 10.100.1.5:9009`
(HTTP health check on `/health`), and the per-backend security-group rule opens 9009.
Reach it at `http://103.245.254.29:9009`.

## Dependencies
- Postgres (`../postgres`) — schema `leo_ads` in the `customer360` DB.
- Redis (`../cache`) — response cache; uat reuses the co-located container (`127.0.0.1:6580`).
- (optional) Dagster — the app carries a `dagster-graphql` client for submitting jobs; not required to boot.
