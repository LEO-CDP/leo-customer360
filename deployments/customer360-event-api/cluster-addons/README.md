# Cluster add-ons

Cluster-wide pieces the event-api deployment benefits from but does **not** own.

They live beside the app rather than inside `../base/` on purpose: each one installs into
`kube-system` or its own namespace and registers ClusterRoles or APIServices. The scoped CI
identity from [`../ci-access/`](../ci-access) deliberately cannot do that, so folding them
into the deploy would make every CD run fail. Apply them **once, with an admin kubeconfig** —
the same category as the KEDA operator.

```bash
KC=../../server/kubeconfig-vks-uat.yaml
kubectl --kubeconfig $KC apply -k metrics-server     # always: everything else shows blanks without it
kubectl --kubeconfig $KC apply -k portainer-agent    # web UI with username/password
```

## metrics-server

Provides the Metrics API. Without it these are all blank or broken:

* `kubectl top nodes` / `kubectl top pods`
* the resource columns in k9s, Portainer, and any other UI
* **the event-api ScaledObject's `Resource/cpu` trigger** — the CPU safety net cannot read a
  value, so autoscaling silently runs on fewer signals than it appears to

Patched in two ways versus upstream:

* `--kubelet-insecure-tls`, because managed control planes (VKS included) give kubelets
  self-signed serving certs that the cluster CA does not cover. Without it every scrape fails
  `x509: cannot validate certificate`, metrics-server never goes Ready, and the APIService
  stays unavailable — which reads like a broken install rather than a TLS mismatch.
* memory request 200Mi → 100Mi. Upstream sizes for a large cluster; this is one node.

## Portainer agent

Registers this cluster as an environment in the Portainer CE already running on the api box
(`../../monitoring`, `portainer_sso = false` → local admin/password). After applying:
**Environments → Add environment → Kubernetes → Agent**, address `<node-private-ip>:30778`.

Two things to know:

* **It binds cluster-admin.** That is what lets Portainer manage rather than just display,
  and it means Portainer's admin password now guards this cluster too.
* **The likely snag is the node's security group, not the manifest.** `extra_ingress` in
  `../../server/overlays/<env>.tfvars` applies to the Default secgroup on the vServers;
  the VKS nodes have their own VKS-managed group. If Portainer cannot reach `:30778`, open it
  there — console, or the `security_groups` attribute on `vngcloud_vks_cluster_node_group`.

## What none of this gives you

These are **operational** UIs: current state, logs, events, live resource usage. They are not
monitoring systems — no history, no trends, no alerting. For that you want Grafana against a
real Prometheus, which needs ~1.5–2 GiB and therefore a second node. Today, historical
signal lives outside the cluster: Netdata for host metrics and Jaeger for request traces,
both on the api box.

## Memory budget

This cluster is one `s-general-2x4` node, and memory is the binding constraint — the kubelet
reserves ~1.3 GiB regardless of node size, leaving 2590Mi allocatable.

| | request |
|---|---|
| already committed (system + KEDA + event-api + Prometheus) | ~2184Mi |
| metrics-server | 100Mi |
| Portainer agent | 64Mi |
| **remaining** | **~242Mi** |

That fits, but with little room. Two consequences worth planning around:

* **Scaling event-api to its max of 3 pods costs another ~256Mi**, which this node does not
  have alongside both add-ons. Add a node (`num_nodes` in `deployments/vks/overlays/uat.tfvars`)
  before relying on the full 1–3 range, or the extra replicas sit `Pending` while the
  ScaledObject reports the scale-up as successful.
* **Grafana + a full Prometheus stack (~1.5–2 GiB) is out of reach entirely** on one node.
