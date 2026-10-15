# deployments/customer360-backend — Dagster orchestrator on Kubernetes (GreenNode VKS)

`customer360-backend` is the Dagster orchestrator (webserver + daemon + all code locations,
`dagster dev`, :3000). It used to run as one docker container on the vServer jobs box
(`../server/deploy-backend.sh`). This directory runs it on VKS as a **singleton Deployment**,
using the same layout as [`../customer360-promotions`](../customer360-promotions/README.md).

Plan and phases: [`../docs/dagster-vks-adaptation-plan.md`](../docs/dagster-vks-adaptation-plan.md).

## Layout

```
deploy-backend-vks.sh      <uat|prod> [deploy|destroy] — renders generated/<env>, applies, verifies
overlays/<env>.tfvars      app CONFIG: port, metadata DB name, max concurrent runs, require S3
k8s/
  base/dagster.yaml        Deployment (replicas 1, Recreate) + ClusterIP Service :3000
  overlays/uat/            UAT sizing  (limit 1.5 GiB — the 2x4 nodes are tight)
  overlays/prod/           PROD sizing (2-4 GiB)
  generated/<env>/         rendered by the script — GITIGNORED, holds credentials
```

## Deploy

```bash
cd deployments/customer360-backend
export KUBECONFIG=../kubeconfig-vks-uat.yaml        # explicit; never the current context
IMAGE_TAG=sha-<40-char-sha> ./deploy-backend-vks.sh uat
./deploy-backend-vks.sh uat destroy                 # remove the workload (DB + logs are kept)
```

Inputs come from the same Terraform state the vServer deploy used (`../postgres`, `../storage`,
`../cache`, `../server/smtp.<env>.env`). Without terraform installed, set `DB_HOST` and
`REDIS_HOST`, and `TF_VAR_db_password` / `TF_VAR_access_key` / `TF_VAR_secret_key` /
`TF_VAR_redis_password`. The image is the CI-built `ghcr.io/leo-cdp/leo-customer360/customer360-dagster`
(tags `sha-<long sha>` and `latest`); the shared `ghcr-pull` secret is reused if present.

The script then verifies from inside the pod: the `[render-instance]` log shows PostgreSQL
storage and S3 compute logs, and GraphQL reports every code location `LOADED`.

## Differences from the other VKS services (and why)

- **No HPA, no PDB, `Recreate`.** `dagster dev` embeds the daemon; two pods = duplicate schedule and
  sensor ticks. Scaling out means the webserver/daemon split (scaling-analysis §5), not replicas.
- **ClusterIP, not LoadBalancer.** The API reaches `dagster:3000` in-cluster. The UI has no auth, so
  use `kubectl port-forward svc/dagster 3000:3000`.
- **Redis is the api box's, not local.** The vServer backend ran its own redis on the jobs box. Pods
  cannot, so this points at the api box's private redis; keys (analytics dedup) start empty.
- **Memory is the UAT constraint.** The 2x4 nodes have ~2.6 GiB allocatable and already run event-api,
  promotions and DaemonSets. If the pod OOMKills, add or enlarge a node
  (`../vks/overlays/uat.tfvars`) rather than raising the limit.

## Cutover

Deploying here does **not** move traffic. The vServer Dagster (`10.100.1.4:3000`) is still the upstream
in `../proxy/overlays/<env>.tfvars` (`dagster_upstream`) and `../load_balancer/overlays/<env>.tfvars`.
Run both side by side on different `dagster_pg_db` values until verified, then repoint and stop the
vServer container. `deploy-all.sh`'s `backend` step is still the vServer path.
