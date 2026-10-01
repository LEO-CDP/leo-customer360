# deployments/customer360-promotions — c360 Promotions on Kubernetes (GreenNode VKS)

Ad decisioning (`GET /serve/{placement}`, `/ads/{id}`, `/placements/{key}`) runs here as an
autoscaled Deployment — **1-2 pods on UAT**, 2-8 on prod. It reads its own **`leo_ads`**
schema in the shared `customer360` database and uses the shared Redis as a response cache.

It used to run as a single docker container on the shared api box (uat) with a dedicated
`ads` vServer planned for prod. Kubernetes now owns replication, restarts, rollouts and
scaling. The cluster, the GHCR pull secret, the namespace and the CI identity are shared
with [`customer360-event-api`](../customer360-event-api/README.md), which migrated
first — see its [runbook](../docs/vks-event-api-migration-runbook.md) for the cluster-level
background. Where this service deliberately differs from it is covered below.

## Layout

```
deploy-promotions.sh             renders generated/<env> and applies it
overlays/<env>.tfvars     app CONFIG: port, root_path, schema, DB pool, otel
k8s/
  base/promotions.yaml    every resource: Deployment + Service (LoadBalancer :9009)
                          + PodDisruptionBudget + HorizontalPodAutoscaler
  overlays/uat/           UAT profile:  1-2 pods
  overlays/prod/          PROD profile: 2-8 pods, bigger pods
  generated/<env>/        rendered by ./deploy-promotions.sh — GITIGNORED, holds credentials
```

Three layers: `k8s/base/` is env-agnostic, `k8s/overlays/<env>/` carries the committed
per-env **scaling** profile (so a capacity change is a reviewable diff), and
`k8s/generated/<env>/` is what actually gets applied. Per-env **configuration** stays in
`overlays/<env>.tfvars`, where it already was.

To change capacity, edit the k8s overlay and redeploy:

```yaml
# k8s/overlays/uat/kustomization.yaml
- { op: replace, path: /spec/maxReplicas, value: 4 }
```

## Deploy

```bash
cd deployments/customer360-promotions
export KUBECONFIG=../server/kubeconfig-vks-uat.yaml   # from https://vks.console.greennode.ai
export GHCR_PULL_TOKEN=<a token with read:packages>   # the image is private
./deploy-promotions.sh uat
./deploy-promotions.sh uat destroy     # remove the workload (namespace and data are kept)
```

`deploy-promotions.sh` resolves the DB host from `../postgres` and the Redis host from
`../cache` + `../server` — the same Terraform state the vServer deploy used — renders them
into a hash-suffixed ConfigMap/Secret, pins the image by digest, and applies the overlay.

## The schema is NOT bootstrapped by this script

`deploy-promotions.sh` used to psql `sql-scripts/db-schema-init.sql` onto the box it deployed to.
There is no box any more. `../postgres/run-sql.sh` already reaches the private vDB through
the bastion, so the `leo_ads` schema moved there and runs as part of the **`db-schema`**
step of `deploy-all.sh`:

```bash
../postgres/run-sql.sh uat                        # schema + demo data (uat default)
PROMOTIONS_SEED_SAMPLE=false ../postgres/run-sql.sh uat  # schema only
```

Both files are idempotent (`CREATE ... IF NOT EXISTS`, `ON CONFLICT DO NOTHING`), so this
is safe to re-run. `PROMOTIONS_SEED_SAMPLE` defaults to `true` on uat and `false` on prod, which
is what the old `ads_seed_sample` overlay flag did.

Run it **before** the first deploy into a fresh database: the app raises on startup when it
cannot reach Postgres, so missing tables surface as `CrashLoopBackOff`, not as a 500.

## Expose it

The Service is `type: LoadBalancer` on 9009, so Caddy's `ads_upstream` keeps its `ip:9009`
shape. After the first deploy:

```hcl
# ../proxy/overlays/uat.tfvars
ads_upstream = "<EXTERNAL-IP>:9009"   # was 127.0.0.1:9009
```

then `../proxy/deploy-caddy.sh uat`. Caddy forwards `/ads/*` **unstripped** and the app
sets `root_path=/ads`, so the public URL shape does not change.

`ads_upstream` accepts several space-separated addresses, so
`"127.0.0.1:9009 <EXTERNAL-IP>:9009"` canaries the new pods alongside the old container.

## Autoscaling: a plain HPA, not KEDA

event-api scales on real ingest requests/sec via a KEDA `ScaledObject` reading a bundled
Prometheus. This service exposes **no metrics**, so that trigger would have nothing to
query and KEDA would collapse to its CPU trigger — which is a `HorizontalPodAutoscaler`
with an operator and two CRDs in front of it. metrics-server is already installed
cluster-wide, so plain CPU utilisation works today.

Swap in a KEDA ScaledObject once the app grows a `/metrics` endpoint;
`../customer360-event-api/base/event-api.yaml` is the template.

## Dependencies

- **Postgres** (`../postgres`) — schema `leo_ads` in the `customer360` DB. **Required**:
  `PromotionsApplication._check_database()` raises on startup when it is unreachable.
  `leo_ads` has no RLS, so no `app.tenant_id` / `PGOPTIONS` is needed.
- **Redis** (`../cache`) — response cache, reached at the api box's **private VPC address**
  (not `127.0.0.1`: the pods no longer run on that box). The app fails open without it.
- **VKS node group** (`../vks`) — the pods must sit in the same subnet as the vServers to
  reach the private DB and Redis.

Each pod opens its own SQLAlchemy pool, so DB connections scale with replicas —
`promotions_db_pool_size` / `promotions_db_max_overflow` in the overlay keep the ceiling affordable.

## prod

`../vks/overlays/prod.tfvars` is still `cluster_id = "CHANGE_ME"` — **there is no prod VKS
cluster**, so `./deploy-promotions.sh prod` has nowhere to go. The prod overlay is committed so
the capacity profile is reviewable and the cutover is a deploy rather than a design task.
