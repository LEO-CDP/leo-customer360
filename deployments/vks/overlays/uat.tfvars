# UAT overlay — environment-specific, NON-SECRET config.
# Secrets (client_id, client_secret) live in ../terraform.tfvars or ../.env.
# Apply with:  ./deploy.sh uat <plan|apply>   (Terraform workspace "uat").

# The cluster created in the VKS console:
# https://vks.console.greennode.ai/k8s-cluster/detail/k8s-1f88905a-95d5-422e-9d18-a6363d3cfd51
cluster_id = "k8s-1f88905a-95d5-422e-9d18-a6363d3cfd51"

# Owns the SSH key this module registers. Same project as ../server/overlays/uat.tfvars.
project_id = "pro-8986f5c6-02ca-4647-be9a-4070bb100559"

# ssh_key_id is intentionally unset: the module registers a key from ssh_public_key
# (in terraform.tfvars) and uses its id. Set it only to reuse an existing console key.

node_group_name = "c360-uat-nodes"

# TWO nodes. Scaling OUT rather than up is deliberate:
#   * `num_nodes` is an in-place update; `flavor_id` is ForceNew, so scaling UP destroys and
#     recreates the node group — every pod dies. Verified with `terraform plan`.
#   * One node has no redundancy: a node failure or a drain during a cluster upgrade takes
#     ingest down, and dropped beacons are gone (unlike queue-backed work that replays).
#     At one node the PodDisruptionBudget and topology-spread constraint are decorative.
#   * UAT then exercises the same multi-node topology as prod.
#
# The cost is real: two small nodes pay the fixed overhead TWICE — ~1.3 GiB of kubelet
# reservation and ~0.5 GiB of DaemonSets (cilium, envoy, csi-node) per node. One 4x8 node
# would give more usable memory than two 2x4s. Availability won that trade; revisit if the
# bill matters more than a node failure.
num_nodes = 2
disk_size = 40

# Public workers: the pods must pull from ghcr.io and write to vStorage. Only set this
# true once the VPC has a NAT gateway (see the variable's description).
enable_private_nodes = false

# --- CHANGE_ME: opaque ids, copy from the VKS console (no Terraform data source exists) ---
# Console -> cluster -> Node groups -> Create, and read the ids off the form.
# s-general-2x4 (2 vCPU / 4 GB) — the same flavor the `docs` vServer uses.
# Resolved from ../server state: data.vngcloud_vserver_flavor.this["docs"].
flavor_id = "flav-d1e54dcd-0565-11f0-a0a4-ec2a72332f83"
# MUST be in the same VPC as the ../server vServers — the pods need 10.100.1.5:6580 (Redis).
# The SAME subnet the vServers run in (network net-d25c55be-…, zone HCM03-1C),
# read from ../server state: vngcloud_vserver_server.this["api"].subnet_id.
# This is what puts the pods on 10.100.1.0/24 alongside the api box, so they can
# reach its private Redis on :6580 — the Redis Streams queue event-api needs.
subnet_id = "sub-7c1f6eff-7244-4a29-a3cf-3592745ea0e7"
