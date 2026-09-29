# Worker node group for the existing GreenNode VKS cluster.
#
# SCOPE: this module manages the NODE GROUP only. The cluster itself was created in
# the console and is referenced by id (var.cluster_id) — Terraform does not own it,
# so nothing here can destroy the control plane.
#
# Why this module exists: a VKS cluster with no node group has a healthy control
# plane and zero capacity. Every system pod (cilium, coredns, the CSI and
# load-balancer controllers) sits Pending, so there is no in-cluster DNS and a
# Service of type LoadBalancer never gets an EXTERNAL-IP. customer360-event-api
# cannot be deployed until this exists.
# Register the workers' SSH key rather than making someone hunt an id in the console.
# ../server does not create one (create_ssh_key = false — it installs the login key via
# cloud-init instead), so there is no existing key to reuse; reusing the same PUBLIC KEY
# means one private key opens both the vServers and the Kubernetes nodes.
resource "vngcloud_vserver_sshkey" "workers" {
  project_id = var.project_id
  name       = coalesce(var.ssh_key_name, "${var.node_group_name}-key")
  public_key = var.ssh_public_key
}

resource "vngcloud_vks_cluster_node_group" "workers" {
  cluster_id = var.cluster_id
  name       = var.node_group_name

  num_nodes = var.num_nodes
  # NOTE: image_id is intentionally absent. The provider marks it
  # Optional+Computed and derives the node image from the cluster —
  # a configured value is ignored (verified: an explicit -var still
  # plans as "known after apply"). Do not add it back as a knob.
  flavor_id  = var.flavor_id
  ssh_key_id = vngcloud_vserver_sshkey.workers.id
  subnet_id  = var.subnet_id

  disk_size = var.disk_size

  enable_private_nodes = var.enable_private_nodes

  lifecycle {
    # Replacing a node group drains every pod on it. Require the change to be
    # explicit (terraform taint / -replace) rather than an accidental diff on an
    # attribute the provider treats as ForceNew.
    prevent_destroy = false # flip to true once this is carrying production traffic
  }
}
