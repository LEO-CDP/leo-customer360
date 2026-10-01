# customer360-event-api on VKS — as-built runbook

Companion to [`vks-migration-technical-analysis.md`](./vks-migration-technical-analysis.md).
That document is the *plan* for moving the platform to Kubernetes; this one is the *record* of
the first service that actually moved, plus the operations you need to run it.

**Status:** live on UAT, serving 100% of `/data` traffic. The old `tracking` vServer was
destroyed 2026-09-29 — there is no longer a same-day rollback; recovery means re-provisioning
a vServer from Terraform.

---

## 1. What changed

| | before | after |
|---|---|---|
| runs on | `tracking` vServer (`10.100.1.8`), own box | VKS cluster, namespace `customer360` |
| replication | N docker replicas + local nginx LB on `:8010` | `Deployment` + KEDA `ScaledObject` |
| scaling | fixed count, `TRACKING_REPLICAS=<n>` + redeploy | autoscaled 1–3 on ingest RPS, queue lag, CPU |
| exposure | box private IP `:8010` | `Service` type LoadBalancer, same `ip:8010` shape |
| deploy | `deploy-tracking.sh` (ssh + docker run) | `deploy-event-api.sh` (kubectl apply -k) |
| step id | `tracking` | `event-api` (`tracking` still accepted as an alias) |

The `ip:8010` shape was preserved deliberately: Caddy's `data_upstream` only changes address,
so no Caddyfile rewrite, ingress controller, TLS migration or public-URL change was needed.

## 2. As-built

| | |
|---|---|
| cluster | `k8s-1f88905a-95d5-422e-9d18-a6363d3cfd51`, API `122.201.13.126:6443`, k8s v1.32.9 |
| node group | `c360-uat-nodes` — 1 × `s-general-2x4`, node `10.100.1.11` |
| subnet | `sub-7c1f6eff-…` = `10.100.1.0/24` — the SAME subnet as the vServers (see §5) |
| pod CIDR | `192.168.0.0/24` (Cilium; masquerades to the node IP leaving the cluster) |
| namespace | `customer360` |
| image | `ghcr.io/…/customer360-event-api@sha256:f485da32…` (digest-pinned by `lib/ghcr.sh`) |
| Service | LoadBalancer `49.213.73.13:8010` |
| autoscaling | KEDA v2.17.1 — min 1 / max 3; triggers: prometheus 50 rps/pod, redis lag 500/pod, cpu 70% |
| add-ons | metrics-server v0.7.2; Portainer agent (NodePort `30778`) |
| traffic | Caddy -> `49.213.73.13:8010` (100%) |
| rollback | **none** — the `tracking` vServer was destroyed once traffic was verified at 100% |

Where things live:

```
deployments/vks/                                  node group (Terraform)
deployments/server/deploy-event-api.sh            the deploy
deployments/customer360-event-api/
  base/event-api.yaml                             all app resources in one file
  overlays/{uat,prod}/                            per-env scaling profile
  ci-access/                                      least-privilege identity for CD
  cluster-addons/                                 metrics-server, Portainer agent
deployments/proxy/                                Caddy — /data routing lives here
```

## 3. Bring-up from scratch

Order matters; each step fails loudly if a prior one is missing.

```bash
# 1. capacity — a cluster with no node group looks healthy and runs nothing (§6.1)
cd deployments/vks && ./deploy.sh uat apply

# 2. KEDA — cluster-wide operator, one-time, admin kubeconfig
kubectl --kubeconfig ../server/kubeconfig-vks-uat.yaml apply --server-side \
  -f https://github.com/kedacore/keda/releases/download/v2.17.1/keda-2.17.1.yaml

# 3. metrics-server — without it `kubectl top`, every UI graph, and the CPU trigger are blank
cd ../customer360-event-api/cluster-addons
kubectl --kubeconfig ../../kubeconfig-vks-uat.yaml apply -k metrics-server

# 4. VPC — pods reach the api box's private Redis from the NODE subnet
cd ../../ && terraform apply -var-file=overlays/uat.tfvars \
  -target='vngcloud_vserver_secgrouprule.extra["6580-10.100.1.0/24"]'

# 5. CI identity — scoped ServiceAccount + kubeconfig for CD
cd customer360-event-api/ci-access && KUBECONFIG=../../kubeconfig-vks-uat.yaml ./make-kubeconfig.sh

# 6. the app
cd ../.. && KUBECONFIG=./kubeconfig-vks-ci.yaml ./deploy-event-api.sh uat

# 7. traffic — set data_upstream to the Service EXTERNAL-IP, then
cd ../proxy && ./deploy-caddy.sh uat validate && ./deploy-caddy.sh uat
```

## 4. Operations

### Shift the canary

`deployments/proxy/overlays/uat.tfvars`, then `./deploy-caddy.sh uat`:

```hcl
data_lb_policy = "weighted_round_robin 9 1"   # 10% to VKS
data_lb_policy = "weighted_round_robin 5 5"   # 50/50
data_lb_policy = "weighted_round_robin 0 10"  # all VKS
```

Weight order matches upstream order. Caddy rejects a weight count that does not match the
upstream count, so `deploy-caddy.sh` derives an even split when `data_lb_policy` is unset.

### Roll back

**There is no fast rollback any more** — the vServer was destroyed on 2026-09-29. Recovery
means re-adding a `tracking` entry to `../server/overlays/uat.tfvars`, `./deploy.sh uat apply`,
redeploying the app to it, and repointing `data_upstream`. Minutes, not seconds.

Within Kubernetes the quick moves are `kubectl -n customer360 rollout undo deploy/event-api`
(previous image) and re-running `deploy-event-api.sh` with `IMAGE_TAG=sha-<commit>` pinned to
a known-good build.

### Scale

Pod counts and thresholds are committed per-env in `customer360-event-api/overlays/<env>/`,
not environment variables, so a capacity change is a reviewable diff. **Raise the node count
before raising `maxReplicaCount`** — otherwise the autoscaler scales into unschedulable pods
that sit `Pending` while the ScaledObject reports success.

### Retire the vServer — DONE (2026-09-29)

Kept here as the procedure, since prod will need it. Shift the canary to `0 10`, prove the
split (count requests arriving at the pod, not just that Caddy reloaded), then a full
`./deploy.sh uat apply`. Before the cutover, use `-target` for secgroup changes: a full apply
also destroys the tracking box, and while Caddy still lists its address that drops live beacons.

The UAT apply hit an unrelated 400 (`Volume size or volume type must be changed`) *after* the
destroy: the backend box's boot volume had been grown to 50 GB in the console while tfvars
still said 20, and vngcloud cannot shrink a volume. Reconciled by setting tfvars to the live
value. Expect the same class of drift on prod.

### Tear the service down

```bash
./deploy-event-api.sh uat destroy   # label-scoped delete; keeps the shared namespace
```

## 5. The one networking fact that matters

The node group was placed in **the same subnet as the vServers** (`10.100.1.0/24`). That is
not incidental — the pods must reach the api box's private Redis on `:6580`, which is the
Redis Streams queue; without it ingestion returns `503` on every request. Cilium masquerades
pod traffic leaving the cluster to the **node** IP, so the security-group rule is written for
the node subnet, not the pod CIDR:

```hcl
{ port = 6580, cidr = "10.100.1.0/24" },  # api-box Redis   <- VKS nodes
{ port = 4318, cidr = "10.100.1.0/24" },  # api-box Jaeger  <- VKS nodes
```

A `/24` rather than the node's `/32` because nodes get new IPs when replaced.

## 6. Gotchas found during this migration

Each cost real debugging time. Grouped by where they bite.

### 6.1 Cluster / capacity

* **A cluster with no node group answers `kubectl` normally and runs nothing.** Every
  `kube-system` pod sits `Pending`, so coredns is down (no Service DNS), the cloud
  load-balancer controller is down (`type: LoadBalancer` never gets an IP), and there is no
  metrics-server. Check `kubectl get nodes` first on any cluster you did not provision.
* **Size by allocatable, not capacity.** The kubelet reserves ~1.3 GiB *regardless of node
  size*: a 4 GiB node yields 2590Mi allocatable, a 2 GiB node only ~630Mi — less than the
  cluster's own system pods need. The smallest usable node here is 2 vCPU / 4 GB.
* **The vngcloud provider ignores `image_id` on a node group** (Optional+Computed; an explicit
  `-var` still plans as `known after apply`). It is not in the config for that reason.
* **Node group names are 5–15 chars**, validated only at creation — i.e. after other resources
  in the plan already exist. `variables.tf` encodes the rule so it fails at plan time instead.

### 6.2 KEDA

* **One failing trigger takes the whole ScaledObject down.** KEDA could not build the redis
  scaler, so it refused to create the HPA *at all* — there was no CPU-only fallback. A CPU
  trigger protects against stale metrics, not against a scaler that cannot connect.
* **Scalers are cached per `metadata.generation`.** After fixing the firewall, KEDA kept
  replaying the old failure; an annotation does not bump generation, so it changed nothing.
  Either edit `/spec` or restart `keda-operator`.
* **An operator resolves your Service from ITS namespace.** `serverAddress:
  http://event-api-prometheus:9090` resolved in the `keda` namespace and failed
  `no such host`. Cross-namespace callers need the FQDN. Triggers using an IP (redis) are
  unaffected, so the failure looks partial and misleading.
* **Scale queue consumers on `lagCount`, not `pendingEntriesCount`.** Each worker holds at
  most `flush_batch_size` unacked messages, so pending entries scale with *replica count* —
  scaling on them is a positive feedback loop. Lag falls as pods are added.
* **`or vector(0)` in the Prometheus query is load-bearing.** The counter only exists after
  the first ingest request, so an untouched deployment returns an empty result, which makes
  the scaler error rather than report idle.

### 6.3 Identity and credentials

* **An imagePullSecret must outlive the job that created it.** On the vServer path a deploy
  did `docker login` + `docker pull` inside the job, so a short-lived token was fine. In
  Kubernetes the kubelet re-reads the secret on *every later pull* — every scale-up and
  reschedule — and a GitHub Actions `GITHUB_TOKEN` is revoked at job end. CD passes a separate
  long-lived `GHCR_PULL_TOKEN`.
* **Long-lived ServiceAccount tokens need a `kubernetes.io/service-account-token` Secret.**
  `kubectl create token` issues a short-lived one that would break CD later.
* **`kubectl auth can-i` needs `--subresource=exec`**, not `pods/exec`. The slash form answers
  `no` even when the Role grants it — a self-check that reports a false negative is worse than
  no self-check.
* **A leaked client certificate cannot be revoked.** kube-apiserver has no CRL; a new cert does
  not invalidate the old one. This is why CD uses the scoped ServiceAccount kubeconfig (whose
  token *can* be revoked) rather than the console's cluster-admin one.

### 6.4 Application

* **The Redis Stream consumer name must identify the pod.** It was
  `f"tracking-worker-{id(self)}"` — a memory address, which collides across identical
  replicas, letting pods claim each other's in-flight messages. Now hostname+pid; in
  Kubernetes the hostname is the pod name. Fixed in `core/redis_queue.py` with regression tests.
* **Readiness must not gate on a shared, non-blocking dependency.** `/health` answers 503 when
  S3 is unreachable, but with the Redis Stream backend ingestion still works. Probing it would
  have pulled every replica out of service during an S3 blip. The probes use `/`.
* **Bronze object keys are content-addressed** (`uuid5` over event ids), which is what makes
  running N replicas safe without partitioning: concurrent pods cannot collide, and a retry
  rewrites identical bytes to an identical key.

### 6.5 metrics-server

* **`--kubelet-insecure-tls` is required on managed clusters.** Kubelets get self-signed
  serving certs the cluster CA does not cover; without the flag every scrape fails
  `x509: cannot validate certificate` and the APIService never becomes available — which looks
  like a broken install rather than a TLS mismatch.

## 7. Outstanding

* **`maxReplicaCount: 3` overstates capacity by one.** 242Mi free fits a second pod (128Mi),
  not a third. Either set it to 2 or add a node.
* **Register the Portainer environment** — Environments → Add → Kubernetes → Agent,
  `10.100.1.11:30778`. May need tcp/30778 opened on the VKS-managed node security group.
* **Finish the canary** — shift to `0 10`, soak, retire the vServer.
* **Rotate three credentials** exposed during the migration: the GitHub PAT (revocable — do
  this first), the `user_password` in `server/terraform.tfvars`, and the cluster kubeconfig
  (client cert — needs CA rotation or an API-server IP allowlist).
* **`image_tag` is unset for uat**, so deploys resolve `latest`. A config-only redeploy
  silently picked up a newer build. Pass `IMAGE_TAG=sha-<commit>` when changing config without
  changing code; CD already enforces `REQUIRE_IMMUTABLE_TAG=1`.
* **No historical metrics or alerting.** Portainer is an operational UI.
  Grafana + a real Prometheus needs ~1.5–2 GiB, i.e. a second node.
