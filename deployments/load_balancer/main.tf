# ---------------------------------------------------------------------------
# Network (optional): when create_network = true, create a VPC + subnet and
# place the LB in it; otherwise use the existing var.subnet_id.
# Normally you want create_network = false so the LB shares the SAME subnet as
# the backends it fronts (a fresh isolated VPC couldn't route to them).
# ---------------------------------------------------------------------------
resource "vngcloud_vserver_network" "this" {
  count = var.create_network ? 1 : 0

  project_id = var.project_id
  name       = var.network_name
  cidr       = var.network_cidr
  zone_id    = var.zone_id
}

resource "vngcloud_vserver_subnet" "this" {
  count = var.create_network ? 1 : 0

  project_id = var.project_id
  name       = var.subnet_name
  network_id = vngcloud_vserver_network.this[0].id
  cidr       = var.subnet_cidr
  zone_id    = var.zone_id
}

# Resolve the LB package (throughput tier, e.g. "NLB_Small") to its uuid.
# The data source has no name filter — it returns EVERY package for the
# project, so we match var.package_name ourselves below.
data "vngcloud_vlb_lb_packages" "all" {
  project_id = var.project_id
}

locals {
  # Effective subnet: the one we just created, or the existing id.
  subnet_id = var.create_network ? vngcloud_vserver_subnet.this[0].id : var.subnet_id

  # The package_id argument on the LB wants the uuid, not the display name.
  matched_packages = [
    for p in data.vngcloud_vlb_lb_packages.all.packages : p
    if p.name == var.package_name
  ]
  lb_package_id = length(local.matched_packages) > 0 ? local.matched_packages[0].uuid : ""
}

resource "vngcloud_vlb_load_balancer" "this" {
  project_id = var.project_id
  name       = var.lb_name

  # Package id: the lb_packages data source only returns the DEFAULT AZ's packages, whose
  # uuids the create API rejects for other zones — so prefer a direct var.package_id (the
  # zone's full "lbp-..." uuid from `?zoneId=`; the create wants it WITH the prefix).
  package_id = var.package_id != "" ? var.package_id : local.lb_package_id
  scheme     = var.scheme
  type       = var.lb_type

  subnet_id = local.subnet_id
  zone_id   = var.zone_id

  lifecycle {
    # The packages data source returns the FULL list; a typo'd package_name just
    # yields no match and an EMPTY package_id, which otherwise surfaces as a
    # confusing provider-side error. Fail early with an actionable message.
    precondition {
      condition     = local.lb_package_id != ""
      error_message = "No vLB package matched package_name=\"${var.package_name}\" in project ${var.project_id}. Copy the exact package name (e.g. NLB_Small) from the console, or inspect the `lb_packages` output of `terraform plan`."
    }
    precondition {
      condition     = var.create_network || (var.subnet_id != "" && var.subnet_id != "sub-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
      error_message = "No subnet: set create_network=true to create a VPC+subnet, or provide a real subnet_id (sub-...) — ideally the same subnet as the LB's backends."
    }
    precondition {
      condition     = var.project_id != "" && var.project_id != "pro-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
      error_message = "project_id must be your REAL VNG Cloud project id (pro-...), not the placeholder. Find it in the console project selector / overview."
    }
  }
}

# ---------------------------------------------------------------------------
# One pool per backend service (Layer 4 / TCP for the NLB). The member is the
# backend server's PRIVATE ip; health checks hit member_port (HTTP path when
# given, else a plain TCP connect).
# ---------------------------------------------------------------------------
resource "vngcloud_vlb_pool" "this" {
  for_each = var.backends

  project_id       = var.project_id
  load_balancer_id = vngcloud_vlb_load_balancer.this.id
  name             = "${var.lb_name}-${each.key}"
  protocol         = "TCP"
  algorithm        = "ROUND_ROBIN"

  health_monitor {
    health_check_protocol = each.value.health_path != null ? "HTTP" : "TCP"
    health_check_method   = each.value.health_path != null ? "GET" : null
    health_check_path     = each.value.health_path
    success_code          = each.value.health_path != null ? 200 : null
    http_version          = each.value.health_path != null ? "1.0" : null
    healthy_threshold     = 3
    unhealthy_threshold   = 3
    interval              = 30
    timeout               = 5
  }

  members {
    backup       = false
    name         = "${each.key}-member"
    ip_address   = each.value.member_ip
    port         = each.value.member_port
    monitor_port = each.value.member_port
    weight       = 1
  }
}

# One TCP listener per backend: public listen_port -> the pool above.
resource "vngcloud_vlb_listener" "this" {
  for_each = var.backends

  project_id       = var.project_id
  load_balancer_id = vngcloud_vlb_load_balancer.this.id
  name             = "${var.lb_name}-${each.key}"
  protocol         = "TCP"
  protocol_port    = each.value.listen_port
  default_pool_id  = vngcloud_vlb_pool.this[each.key].id
  allowed_cidrs    = "0.0.0.0/0"
}

# Open each backend's app port on its security group so the LB (and clients,
# if the NLB preserves the source IP) can reach it. Skipped when no secgroup id.
resource "vngcloud_vserver_secgrouprule" "backend" {
  for_each = var.backend_security_group_id != "" ? var.backends : {}

  project_id        = var.project_id
  security_group_id = var.backend_security_group_id
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "TCP"
  port_range_min    = each.value.member_port
  port_range_max    = each.value.member_port
  remote_ip_prefix  = var.backend_ingress_cidr
  description       = "LB backend ${each.key} port ${each.value.member_port}"
}
