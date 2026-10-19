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
- **Capacity is the UAT blocker** — see below.

## Cutover

Deploying here does **not** move traffic. The vServers reach Dagster through the node: the UAT overlay makes the
`dagster` Service a NodePort (`30300`) on the node labelled `c360.pool=dagster` (`10.100.1.11`). The Dagster UI has
no auth, so keep that node private and let its security group allow `30300` only from the vServer subnet.
Order (each step is reversible by pointing back at `10.100.1.4:3000`):

1. `./deploy-backend-vks.sh uat` (applies the NodePort).
2. From the api box: `curl -s -o /dev/null -w '%{http_code}\n' http://10.100.1.11:30300/server_info` must print 200.
3. API: `DAGSTER_HOST=10.100.1.11 DAGSTER_PORT=30300 ../server/deploy-api.sh uat` (restarts the api container), and set
   the GitHub Environment variables `DAGSTER_HOST` / `DAGSTER_PORT` to the same so CD keeps it.
4. `../load_balancer/deploy.sh uat apply` and (if the `/dagster` block is on) `../proxy/deploy-caddy.sh uat`; both
   overlays already point at `10.100.1.11:30300`.
5. Stop and remove the vServer Dagster containers (`customer360-backend`, `-daemon`, `-redis`); never run both daemons at once. (Done for uat: the backend vServer was retired.)

**CI/CD.** `deploy-all.sh`'s `backend` step (and so `cd.yml`) deploys to VKS. It needs the existing
`VKS_KUBECONFIG` and `GHCR_PULL_TOKEN` secrets and a node labelled `c360.pool=dagster`. `admin-uat.yml`
(`restart-backend`) restarts the VKS Deployment; the old vServer paths and `vm-prewarm.yml` entry are removed.

## Capacity (vServer parity)

Dagster runs on an `s-general-4x8` vServer today (`../server/overlays/uat.tfvars`), raised from 2x4
after code-server heartbeat deaths and run-worker OOM that hung runs. The UAT VKS nodes are
`s-general-2x4` (~1.8 CPU / ~2.6 GiB allocatable, ~1 GiB free after event-api, promotions and
DaemonSets) — **they cannot host Dagster at parity**. `check-capacity.sh <env>` (read-only) tests
every node for (1) parity with the vServer box and (2) room for the pod's requests, and
`deploy-backend-vks.sh` refuses to apply until one node passes.

To make room there are two routes; both end with a >= 4x8 node labelled `c360.pool=dagster` (the pod's nodeSelector;
VKS does not set it, you do):

- **Resize one existing node (chosen for UAT while 4x8 stock is sold out).** Resize
  `...-19d70`, not `c376c` (infra only: keda, metrics-server, hubble, portainer; event-api/promotions are on `c376c`) to
  `s1-standard-8x24` in the vServer console (+~2.38M VND/mo, ~+1.4M more than a 4x8). The resize reboots the VM, so those
  infra pods are down a few minutes. Open question for VNG: whether the new flavor survives node-group scale, upgrade
  or auto-repair. Then:
  `kubectl label node vks-leocdp-uat-c360-uat-nodes-19d70 c360.pool=dagster`
- **Separate 4x8 node group** (cheaper, ~+946k VND/mo gen-1, ~+1,135k gen-2): `flavor_id` of the existing group is
  ForceNew, so re-flavoring it would recreate it and kill event-api ingest and promotions; add a new group via
  `../vks/overlays/uat-dagster.tfvars` (`cd ../vks && ./deploy.sh uat-dagster apply`) or the console, then label its
  node `c360.pool=dagster`. Blocked while all flavors are SOLD OUT.

Optional taint so only Dagster lands there (the pod already tolerates it; skip it on the resized node, because the
infra pods already scheduled there would not return after a drain):
`kubectl taint nodes -l c360.pool=dagster c360.pool=dagster:NoSchedule`

Then `KUBECONFIG=../kubeconfig-vks-uat.yaml ./check-capacity.sh uat` should pass; deploy.
