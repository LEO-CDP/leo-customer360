# VKS — worker node group

Terraform for the **worker node group** of the GreenNode VKS (managed Kubernetes)
cluster that runs [customer360-event-api](../customer360-event-api/README.md).

The **cluster itself is created in the console** and referenced here by id, so Terraform
owns only the node group — nothing in this module can destroy the control plane.

```bash
cd deployments/vks
cp .env.example .env                           # vStorage keys for the state backend
cp terraform.tfvars.example terraform.tfvars   # same vIAM creds as ../server
# fill the three CHANGE_ME ids in overlays/uat.tfvars (see below)
./deploy.sh uat plan
./deploy.sh uat apply
```

## Why it exists

A VKS cluster with no node group has a healthy control plane and **zero capacity**. The
symptom is not an obvious error — everything simply sits `Pending`:

```
$ kubectl get nodes
No resources found

$ kubectl get pods -A
kube-system   cilium-operator-…                     0/1   Pending
kube-system   coredns-…                             0/1   Pending
kube-system   vngcloud-csi-controller-…             0/7   Pending
kube-system   vngcloud-load-balancer-controller-…   0/1   Pending
```

Three consequences worth recognising, because each produces a confusing downstream error:

* **coredns is down** → nothing in the cluster can resolve a Service name, so the
  event-api ScaledObject cannot reach `event-api-prometheus`.
* **The vngcloud load-balancer controller is down** → a `Service` of `type: LoadBalancer`
  stays `<pending>` forever and never yields the EXTERNAL-IP that Caddy's `data_upstream`
  needs.
* **No metrics-server** → the KEDA CPU trigger has nothing to read.

## The three ids you must copy from the console

The vngcloud provider ships **no VKS data sources**, so these cannot be looked up by name
in Terraform. Read them off the node-group creation form in the VKS console:

| Variable | What it is |
|---|---|
| `flavor_id` | worker vCPU/RAM tier |
| `ssh_key_id` | SSH key attached to the nodes |
| `subnet_id` | the subnet the nodes join — **see the warning below** |

There is deliberately no `image_id`: the provider marks it Optional+Computed and derives the
node image from the cluster, so a configured value is silently ignored (an explicit
`-var image_id=…` still plans as `known after apply`). Don't go hunting for that id.

> **`subnet_id` is load-bearing.** The pods must reach the api box's *private* Redis at
> `10.100.1.5:6580` — that is the Redis Streams queue, and without it
> customer360-event-api returns `503` on every ingest. Choose a subnet in the **same VPC
> as the vServers** in [`../server`](../server); if the VKS cluster sits in a different
> VPC, no security-group rule will make that hop work. Jaeger (`:4318`) has the same
> requirement.

After the nodes are up, open those two ports to the worker-node CIDR in
`../server/overlays/<env>.tfvars` (`extra_ingress`) and apply — the rules there are
commented out precisely because the CIDR is not knowable until this module has run.

## Sizing

Size by **allocatable memory, not node capacity**. Measured on this cluster:

| | per node |
|---|---|
| capacity (s-general-2x4) | 3914 MiB |
| kubelet reservation | ~1324 MiB |
| **allocatable** | **2590 MiB** |
| cluster's own system pods | ~1628 MiB |
| left for workloads | ~960 MiB |

That reservation is roughly **fixed**, so it does not shrink with the node — which is why a
1 CPU / 2 GB node is unusable here: ~630 MiB allocatable against system pods that want
~1628 MiB. The smallest node that works is 2 CPU / 4 GB.

**Scale OUT, not up.** `num_nodes` is an in-place update; `flavor_id` is **ForceNew**, so
moving to a bigger node destroys and recreates the node group and every pod with it. If you
ever want a larger flavor, change it while the cluster is idle — afterwards it costs a
maintenance window.

The trade is not free: two nodes pay the fixed overhead twice — ~1324 MiB of kubelet
reservation and ~496 MiB of DaemonSets (cilium, cilium-envoy, csi-node) **per node**. One
`s-general-4x8` yields more usable memory than two `s-general-2x4`. Redundancy won that
argument here: a node failure or an upgrade drain takes ingest down, and dropped beacons are
gone — unlike queue-backed work, they do not replay. At one node the PodDisruptionBudget and
topology-spread constraint are decorative.

**Raise the node count before raising `maxReplicaCount`** in
`../customer360-event-api/overlays/<env>/`. Otherwise the autoscaler scales into pods
that cannot be scheduled: they sit `Pending` while the ScaledObject reports the scale-up as
having succeeded.

`enable_private_nodes = false` on purpose: the pods pull from `ghcr.io` and write to
vStorage, so private workers need a NAT gateway first.

## Where this sits

| Step | Module |
|---|---|
| 1. Cluster | VKS console (not Terraform) |
| 2. Node group | **this module** |
| 3. KEDA operator | `kubectl apply --server-side -f <keda release>` — one-time, cluster-wide |
| 4. CI identity | [`../customer360-event-api/ci-access/`](../customer360-event-api/ci-access) |
| 5. The app | `../server/deploy-event-api.sh <env>` |
