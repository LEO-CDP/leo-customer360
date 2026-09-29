# customer360-event-api on Kubernetes (GreenNode VKS)

Event ingestion (`POST /data/api/v1/tracking/logs` → Redis Stream → S3/vStorage NDJSON)
runs here as a KEDA-autoscaled Deployment — **1-3 pods on UAT**, 3-20 on prod.

Operational runbook — canary, rollback, teardown, and the gotchas found migrating:
[`deployments/docs/vks-event-api-migration-runbook.md`](../../docs/vks-event-api-migration-runbook.md).

It used to run as N docker replicas behind a local nginx load balancer on a dedicated
`tracking` vServer. Kubernetes now owns replication, restarts, rollouts and scaling, so
that box (and its nginx LB) is gone.

## Layout

```
base/event-api.yaml     every resource for the service, in one file:
                          namespace
                          Deployment + Service (LoadBalancer :8010) + PodDisruptionBudget
                          KEDA ScaledObject — autoscales on ingest requests/sec + backlog
                          a small Prometheus that scrapes the pods to feed the scaler
overlays/uat/           UAT profile:  1-3 pods,  ~50 req/s  + 500 backlog per pod
overlays/prod/          PROD profile: 3-20 pods, ~100 req/s + 1000 backlog per pod, bigger pods
generated/<env>/        rendered by ../deploy-event-api.sh — GITIGNORED, holds credentials
ci-access/              one-time bootstrap: a least-privilege identity for CI
  rbac.yaml               ServiceAccount + Role/ClusterRole it deploys with
  make-kubeconfig.sh      mints a kubeconfig for it -> VKS_KUBECONFIG
```

Three layers: `base/` is env-agnostic, `overlays/<env>/` carries the committed per-env
scaling profile (so a capacity change is a reviewable diff, not an env var someone
remembers to set), and `generated/<env>/` is what actually gets applied.

`../deploy-event-api.sh <uat|prod>` renders that last layer on top of the overlay, filling
the image digest, ConfigMap and Secret from the **same Terraform state the vServer deploy
used** — `../../storage` for the S3 endpoint and keys, `../../cache` + the `server` outputs
for the Redis host and password.

To change capacity, edit the overlay and redeploy:

```yaml
# overlays/uat/kustomization.yaml
- { op: replace, path: /spec/maxReplicaCount, value: 20 }
- { op: replace, path: /spec/triggers/0/metadata/threshold, value: "80" }   # RPS per pod
- { op: replace, path: /spec/triggers/2/metadata/lagCount, value: "800" }   # backlog per pod
```

The trigger indices are positional: **0 = Prometheus RPS, 1 = CPU, 2 = Redis backlog**. Add
new triggers at the end of the list in `base/event-api.yaml` so these patches keep pointing
at the right one.

## Deploy

KEDA must be installed in the cluster first (one-time, cluster-wide). The deploy script
checks and stops with the exact command rather than installing an operator behind your back:

```bash
kubectl apply --server-side -f https://github.com/kedacore/keda/releases/download/v2.17.1/keda-2.17.1.yaml
```

```bash
cd deployments/server
export KUBECONFIG=./kubeconfig-vks-uat.yaml   # from https://vks.console.greennode.ai
export GHCR_TOKEN=<a token with package:read> # the image is private
./deploy-event-api.sh uat                     # add INSTALL_KEDA=1 to let it install KEDA
```

## A least-privilege kubeconfig for CI

The kubeconfig the VKS console hands you is cluster-admin. Handing that to CD means a
compromised workflow owns the whole cluster. `ci-access/` replaces it with an identity that
can deploy this one service into this one namespace.

Run once, with the admin kubeconfig:

```bash
cd deployments/server/customer360-event-api/ci-access
KUBECONFIG=../../kubeconfig-vks-uat.yaml ./make-kubeconfig.sh
gh secret set VKS_KUBECONFIG < ../../kubeconfig-vks-ci.yaml
```

It applies `rbac.yaml`, waits for the token, writes the kubeconfig, and then prints what the
new identity can and cannot do — a kubeconfig that silently still has admin, or silently has
nothing, is worse than none:

```
   create deployments in customer360  : yes
   delete the namespace               : no  (expected: no)
   read secrets in kube-system        : no  (expected: no)
   list nodes                         : no  (expected: no)
```

Two things to know:

* **The token does not expire.** It comes from a `kubernetes.io/service-account-token`
  Secret rather than `kubectl create token`, which issues a short-lived one. A token that
  expires would deploy once and then break CD — the same failure shape as the registry
  token described above. Treat the generated file as a credential; it is gitignored.
* **The identity deliberately cannot install KEDA.** That is a cluster-wide operator
  install, so it stays a human-with-admin action. `deploy-event-api.sh` detects a missing
  KEDA and stops with the command to run; `INSTALL_KEDA=1` will not work with this
  kubeconfig, by design.

The rules in `rbac.yaml` are derived from the calls `deploy-event-api.sh` actually makes —
if you add a step to that script, add its verb there or the deploy fails with `Forbidden`.
One subtlety: the deploy creates a `Role` for the bundled Prometheus, and Kubernetes forbids
granting permissions you do not hold yourself, so the deployer's own `pods` read access is
what makes creating that Role legal.

## Environment

Everything else (S3 endpoint + keys, Redis host + password) is read from Terraform state, so
these are the only variables you pass. `deployments/server/.env` is sourced if present.

**Required**

| Variable | Why |
|---|---|
| `KUBECONFIG` | The VKS cluster is not in the default kubeconfig. Falls back to `./kubeconfig-vks-<env>.yaml`, then `~/.kube/vks-<env>.yaml`. For CI prefer the scoped one from `ci-access/` over the console's admin kubeconfig. |
| `GHCR_PULL_TOKEN` | Becomes the cluster's `ghcr-pull` imagePullSecret. Must be **long-lived** (a PAT with `read:packages`) — see the warning below. Skippable only if the secret already exists in the namespace. |
| `TF_VAR_access_key` / `TF_VAR_secret_key` | vStorage S3 credentials. Read from `../../storage/terraform.tfvars` when set locally; that file is gitignored, so CI must pass them. |
| `TF_VAR_redis_password` | Redis password. Read from `../../cache/terraform.tfvars` locally. Without it the pods start but cannot enqueue, and ingestion returns 503. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Only for the Terraform **remote state** backend on vStorage, to resolve the Redis host. |

**Optional**

| Variable | Default |
|---|---|
| `EVENT_API_NAMESPACE` | `customer360` |
| `KUBE_CONTEXT` | the kubeconfig's current context |
| `INSTALL_KEDA` | `0` — the script reports and stops instead of installing an operator cluster-wide |
| `KEDA_VERSION` | `v2.17.1` (only used by `INSTALL_KEDA=1`) |
| `REDIS_SERVER_KEY` / `MON_SERVER_KEY` | `api` — which `../overlays/<env>.tfvars` server runs Redis / Jaeger |
| `IMAGE_TAG` | `image_tag` from `../overlays/<env>.tfvars`, else `latest` |
| `OTEL_ENABLED` | `otel_enabled` from `../overlays/<env>.tfvars` |
| `S3_AUTO_CREATE_BUCKETS` | `s3_auto_create_buckets` from the storage overlay |
| `EVENT_API_RATE_LIMIT_RPS` *(or `_REQUESTS` + `_WINDOW_SECONDS`)* | the app default, 120 req / 60s per client IP |

Pod counts and scaling thresholds are **not** environment variables — they live in
`overlays/<env>/`.

> **The pull token must outlive the job.** On the vServer path a deploy did `docker login`
> and `docker pull` inside the job, so a short-lived token was fine. Kubernetes instead
> *stores* the imagePullSecret and the kubelet re-reads it on every later pull — every
> scale-up, reschedule and node replacement. A GitHub Actions `GITHUB_TOKEN` is revoked when
> its job ends, so a secret built from it deploys fine and then breaks the next pull with
> `ImagePullBackOff`. That is why CD passes a separate `GHCR_PULL_TOKEN`; the script warns
> if it falls back to a job token.

Check what it is scaling on:

```bash
kubectl -n customer360 get scaledobject event-api      # READY/ACTIVE
kubectl -n customer360 get hpa keda-hpa-event-api      # current vs target RPS
```

Then point Caddy at it — the Service keeps the old `ip:8010` shape, so only the address
changes:

```bash
kubectl -n customer360 get svc event-api          # copy EXTERNAL-IP
# set data_upstream = "<EXTERNAL-IP>:8010" in ../proxy/overlays/uat.tfvars
cd ../proxy && ./deploy-caddy.sh uat
```

## Why 3 pods is safe

The write path is `request → Redis Stream (XADD) → worker thread → S3`:

* The Bronze object key is a `uuid5` over the event ids, so two pods writing at the same
  time produce different keys — and an identical batch produces an identical key, making
  a retry idempotent rather than a duplicate.
* The stream consumer group (`s3-writers`) hands each batch to exactly one pod, and
  `TRACKING_STREAM_CLAIM_IDLE_MS` (60s) lets a surviving pod reclaim a dead pod's
  in-flight batches.
* Each pod registers under its own consumer name (hostname + pid), so replicas never
  claim each other's pending messages.

This only holds with `TRACKING_QUEUE_BACKEND=redis_stream`, which the generated ConfigMap
pins. The `memory` backend keeps the queue inside one process and is single-pod only.

## Notes

* **Probes hit `/`, not `/health`.** `/health` answers 503 when S3 is unreachable, but with
  the Redis Stream backend ingestion still works (batches wait in the stream), so gating
  readiness on it would pull all three pods out of service during an S3 blip. `/health`
  is still the right endpoint for monitoring — the deploy script prints it at the end.
* **Graceful drain.** `terminationGracePeriodSeconds: 60` plus a 5s `preStop` sleep: the
  pod leaves the Service endpoints first, then the worker finishes its in-flight batch.
* **Config changes roll the pods.** The ConfigMap and Secret are hash-suffixed, so editing
  a value renames the object and triggers a rollout instead of silently doing nothing.
* **Autoscaling is KEDA on real request rate.** The trigger query is
  `sum(rate(tracking_ingestion_requests_total[1m])) or vector(0)` and KEDA computes
  `ceil(query / threshold)`, so the overlay's `threshold` reads as *target requests/sec per
  pod*. The `or vector(0)` is load-bearing: the counter only exists once a pod has served
  its first ingest request, so before any traffic the query returns an empty result, and an
  empty result makes the scaler error rather than report idle.
* **A CPU trigger sits behind it as a safety net.** KEDA takes the highest replica count any
  trigger asks for, so CPU never holds the RPS trigger back — it just stops a Prometheus
  outage from freezing scaling altogether.
* **There is no hand-written HPA.** KEDA creates and owns `keda-hpa-event-api`; a second HPA
  on the same Deployment would fight it. The deploy script deletes the legacy `event-api`
  HPA if it finds one left over from the pre-KEDA layout.
* **The bundled Prometheus is not a monitoring system.** It scrapes only these pods, keeps
  6h in an `emptyDir`, and exists solely to answer KEDA's query. If you later install a real
  cluster monitoring stack, repoint `serverAddress` at it and delete `base/prometheus.yaml`.
* **A third trigger watches queue backlog**, the thing RPS cannot see: if S3 writes slow
  down, request rate looks calm while the Redis Stream backs up. It scales on the consumer
  group's **lag** — entries added to the stream that the group has not been handed yet.
* **Why lag and not `pendingEntriesCount`.** Each worker reads at most
  `TRACKING_LOG_FLUSH_BATCH_SIZE` (200) messages before acking them, so pending entries are
  bounded by `pods x 200` and *rise* with replica count. Scaling on that is a positive
  feedback loop — more pods produce more pending entries, which asks for more pods. Lag
  falls as pods are added, which is the direction an autoscaler needs. (`lag` comes from
  `XINFO GROUPS` and needs Redis >= 7; this cluster runs Redis 8.)
* **No `TriggerAuthentication` is needed.** The trigger uses `hostFromEnv` / `portFromEnv` /
  `passwordFromEnv`, and KEDA resolves those through the Deployment's own `envFrom` — which
  also sidesteps the hash-suffixed Secret name, since KEDA follows the reference rather than
  a literal name. `TRACKING_STREAM_NAME` / `TRACKING_STREAM_GROUP` are pinned explicitly in
  the generated ConfigMap so the app and the trigger can never end up on different streams.
* **Network prerequisite.** The pods must reach the api box's private Redis (`:6580`) and
  Jaeger (`:4318`) across the VPC. Open those to the VKS worker-node CIDR in
  `../overlays/<env>.tfvars` (`extra_ingress`) — the old rules were scoped to the deleted
  tracking box's `/32` and will not match. The deploy script's closing health check reports
  whether the pods actually got through.
