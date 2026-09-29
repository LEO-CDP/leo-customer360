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

# ONE node, deliberately. Measured on this cluster: a node reserves ~1.3 GiB for the
# kubelet whatever its size (4 GiB capacity -> 2590 MiB allocatable), and the cluster's
# own system pods request ~1628 MiB of that. A second node buys redundancy but the
# workload is 1-3 small pods, so this trades node HA for cost — accepted for UAT only.
# PROD keeps multiple nodes.
#
# Leaves ~960 MiB free. That covers event-api (1-3 x 128Mi) + Prometheus + KEDA, but it
# IS tight at 3 pods: if pods start going Pending, add a node before anything else.
num_nodes = 1
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
