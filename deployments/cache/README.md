# deployments/cache — Redis for customer360-api

Provisions the Redis the API uses for its response cache, auth brute-force
throttle, and rate limiter (all **fail-open** — see `customer360-api/core/cache.py`,
`core/auth.py`, `core/utils/rate_limiter.py`). Two strategies, one per env:

| Env    | Strategy | Where | Cost |
|--------|----------|-------|------|
| `uat`  | **Docker container** built from the repo `./redis` (`redis:8-alpine` + cache-tuned conf) on the api server VM, `--network host`, `:6580` | co-located on `c360-api-uat-api` | free (uses the existing box) |
| `prod` | **Managed VNG MemStore** (Redis), package `db.s-general-2x4` (2 vCPU / 4 GB) | its own managed instance in the VPC | managed-service pricing |

Both use the **same** `redis_password` (shared with the API's `REDIS_PASSWORD`),
kept in git-ignored `terraform.tfvars` / `.env`.

## Setup

```bash
cp terraform.tfvars.example terraform.tfvars   # set redis_password (+ client_id/secret for prod)
```

## UAT — Docker container on the api box

```bash
./deploy.sh uat            # pull + (re)run the Redis container on the api VM
./deploy.sh uat destroy    # remove it (keeps the c360-redis-data volume)
```

No Terraform is involved. `deploy.sh` discovers the api box's public IP from the
sibling `../server` outputs (server key `api`), ships the repo `./redis` build
context (`redis_build_context`), builds `customer360-redis:local` on the box, and
runs it — the image's `ENTRYPOINT` starts `redis-server` with the baked
`redis.conf` (port 6580, `appendonly`, `maxmemory 256mb allkeys-lru`), and
`deploy.sh` appends `--requirepass` at runtime, exactly like docker-compose:

```
docker run -d --name c360-redis --restart unless-stopped --network host \
  -v c360-redis-data:/data customer360-redis:local --requirepass <redis_password>
```

Set `redis_build_context = ""` in the overlay to pull a plain image instead of
building. Because both the Redis and the API containers use `--network host`, the
API reaches Redis at `127.0.0.1:6580`.

## PROD — managed MemStore

Prod compute/DB are not deployed yet. Before applying, set `subnet_id` in
`overlays/prod.tfvars` to the prod api subnet and confirm the `engine_version` /
`package_name` exist in the console MemStore create form (HCM03-1C).

```bash
./deploy.sh prod plan
./deploy.sh prod apply
```

> **Unverified:** the managed path uses `engine_type = "Redis"` and
> `engine_version = "7.0"` from the provider docs; these have not been applied
> against the live catalog. A wrong name/version fails fast via a precondition
> ("No vDB package matched…") — copy the exact values from the console then.

## Wiring the API to the cache

After deploying Redis, (re)deploy the API so it picks up `REDIS_*`:

```bash
../server/deploy-api.sh <env>
```

`deploy-api.sh` reads `redis_password` from this deployment and injects
`REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `CACHE_ENABLED=true` into the
api container:

- **uat** → `REDIS_HOST=127.0.0.1`, `REDIS_PORT=6580` (the co-located container).
- **prod** → `REDIS_HOST` / `REDIS_PORT` from this deployment's Terraform outputs
  (`redis_host`, `redis_port`).

If no `redis_password` is found, the API is deployed with caching **disabled**
(it fails open — still fully functional, just uncached).
