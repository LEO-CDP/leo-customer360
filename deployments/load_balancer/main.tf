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

  package_id = local.lb_package_id
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
