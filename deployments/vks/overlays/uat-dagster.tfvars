# UAT — dedicated node group for customer360-backend (Dagster). Separate workspace/state from
# overlays/uat.tfvars so adding it never touches the event-api/promotions node group
# (changing that group's flavor_id is ForceNew and would recreate it).
# Apply with:  ./deploy.sh uat-dagster <plan|apply>   (Terraform workspace "uat-dagster").
#
# WHY: Dagster runs on an s-general-4x8 vServer today (../server/overlays/uat.tfvars). The UAT
# nodes are 2x4 (~2.6 GiB allocatable, ~1 GiB free) and cannot host it. See
# ../customer360-backend/README.md "Capacity".

cluster_id = "k8s-1f88905a-95d5-422e-9d18-a6363d3cfd51"
project_id = "pro-8986f5c6-02ca-4647-be9a-4070bb100559"

# VKS stamps every node with vks.vngcloud.vn/nodegroup=<this name>; the Dagster pod selects on it.
node_group_name = "c360-uat-dagster"
ssh_key_name    = "c360-uat-dagster-key"

# ONE node: Dagster is a singleton, so a second node would only idle. A node failure reschedules the
# pod (Recreate); the same single point of failure the vServer box has today.
num_nodes = 1
disk_size = 40
enable_private_nodes = false

# CHANGE_ME: the 4x8 flavor id, from the VKS console (Node groups -> Create). Match the vServer box's
# family (s-general-4x8). Existing 2x4 nodes use flav-d1e54dcd-0565-11f0-a0a4-ec2a72332f83.
flavor_id = "CHANGE_ME"
# Same subnet as the existing nodes and the vServers (reaches the DB and the api box's Redis).
subnet_id = "sub-7c1f6eff-7244-4a29-a3cf-3592745ea0e7"
