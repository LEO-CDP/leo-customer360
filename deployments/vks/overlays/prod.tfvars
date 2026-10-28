# PROD overlay — environment-specific, NON-SECRET config.
# Secrets (client_id, client_secret) live in ../terraform.tfvars or ../.env.
# Apply with:  ./deploy.sh uat <plan|apply>   (Terraform workspace "uat").

# The cluster created in the VKS console:
# Create the prod cluster in the VKS console first, then paste its id above.
cluster_id = "CHANGE_ME" # the prod VKS cluster id

# Same account/project as uat and ../server/overlays/prod.tfvars for now.
project_id = "pro-8986f5c6-02ca-4647-be9a-4070bb100559"

node_group_name = "c360-prod-nodes"

# 2 nodes is the floor that fits the cluster's system pods (cilium, coredns x2, CSI
# controller, vngcloud load-balancer controller) plus KEDA, the bundled Prometheus and
# customer360-event-api's 3 replicas — with room for the HPA to scale to ~6 before
# adding a node. Raise this before raising maxReplicaCount in the event-api overlay.
# Pinned so renaming the node group never churns the key (the default derives
# it from node_group_name, which would force a replacement).
ssh_key_name = "c360-prod-nodes-key"

num_nodes = 3
disk_size = 40

# Public workers: the pods must pull from ghcr.io and write to vStorage. Only set this
# true once the VPC has a NAT gateway (see the variable's description).
enable_private_nodes = false

# --- CHANGE_ME: opaque ids, copy from the VKS console (no Terraform data source exists) ---
# Console -> cluster -> Node groups -> Create, and read the ids off the form.
# PROD uses the gen-2 (s2-general) family — its smallest tier is 2x4, so this is NOT
# the same flavor id as uat. Read it from the prod cluster's node-group form.
flavor_id = "CHANGE_ME"
# MUST be in the same VPC as the ../server vServers — the pods need 10.100.1.5:6580 (Redis).
# Must be in the same VPC as the PROD vServers (10.101.x) so the pods reach its Redis.
subnet_id = "CHANGE_ME"
