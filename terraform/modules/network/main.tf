terraform {
  required_providers {
    vngcloud = {
      source = "vngcloud/vngcloud"
    }
  }
}

# Dedicated VNG network + subnet for the Customer360 data stack.
#
# This module only CREATES resources when `create_network = true`. When it is
# false the module produces nothing and the stack falls back to the pre-existing
# `network_id` / `subnet_id` passed in from the environment (the default).
#
# Everything here is fully idempotent: Terraform tracks the network/subnet in
# state and a re-apply with an unchanged config is a no-op.

resource "vngcloud_vserver_network" "this" {
  count = var.create_network ? 1 : 0

  project_id = var.project_id
  name       = "${var.name_prefix}-net"
  cidr       = var.network_cidr
  zone_id    = var.zone_id
}

resource "vngcloud_vserver_subnet" "this" {
  count = var.create_network ? 1 : 0

  project_id = var.project_id
  name       = "${var.name_prefix}-subnet"
  network_id = vngcloud_vserver_network.this[0].id
  cidr       = var.subnet_cidr
  zone_id    = var.zone_id
}
