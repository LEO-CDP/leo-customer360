output "node_group" {
  description = "The managed worker node group."
  value = {
    id        = vngcloud_vks_cluster_node_group.workers.id
    name      = vngcloud_vks_cluster_node_group.workers.name
    num_nodes = vngcloud_vks_cluster_node_group.workers.num_nodes
  }
}
