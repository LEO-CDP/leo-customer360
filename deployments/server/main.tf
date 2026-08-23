# ---------------------------------------------------------------------------
# Network (optional): when create_network = true, create a VPC + subnet and
# place the servers in it; otherwise use the existing var.network_id/subnet_id.
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

# ---------------------------------------------------------------------------
# Catalog resolution (name -> id). The flavor/image/volume-type data sources
# each need a ZONE UUID, which is itself looked up from a zone display name.
# ---------------------------------------------------------------------------
data "vngcloud_vserver_flavor_zone" "this" {
  count = var.flavor_zone_id == "" ? 1 : 0

  name       = var.flavor_zone_name
  project_id = var.project_id
}

data "vngcloud_vserver_volume_type_zone" "this" {
  count = var.root_disk_type_id == "" ? 1 : 0

  name       = var.volume_type_zone_name
  project_id = var.project_id
}

# OS image lookup by name — SKIPPED when var.image_id is set. The data source
# requires the image to be associated with the flavor zone, but the OS images are
# NOT associated with the s2-general "General Purpose" zone, so pass a direct
# img-... via var.image_id (from discover-catalog.py) instead.
data "vngcloud_vserver_image" "this" {
  count = var.image_id == "" ? 1 : 0

  name           = var.image_name
  project_id     = var.project_id
  flavor_zone_id = local.flavor_zone_id
}

# Root-disk volume type — SKIPPED when var.root_disk_type_id is set. The name lookup
# resolves the volume_type_zone by name, which returns the DEFAULT AZ's zone (often a
# disabled one like HCM03-1A) — so pass a direct vtype-... via var.root_disk_type_id.
data "vngcloud_vserver_volume_type" "this" {
  count = var.root_disk_type_id == "" ? 1 : 0

  name                = var.root_disk_type_name
  project_id          = var.project_id
  volume_type_zone_id = try(data.vngcloud_vserver_volume_type_zone.this[0].id, "")
}

# Per-server compute flavor (CPU/RAM tier), one lookup per servers-map entry.
data "vngcloud_vserver_flavor" "this" {
  for_each = var.servers

  name           = each.value.flavor_name
  project_id     = var.project_id
  flavor_zone_id = local.flavor_zone_id
}

# ---------------------------------------------------------------------------
# Optional: register a fresh SSH key and attach it to every server.
# ---------------------------------------------------------------------------
resource "vngcloud_vserver_sshkey" "this" {
  count = var.create_ssh_key ? 1 : 0

  project_id = var.project_id
  name       = var.ssh_key_name
  public_key = var.ssh_public_key
}

locals {
  # Effective network/subnet: the freshly created pair, or the existing ids.
  network_id = var.create_network ? vngcloud_vserver_network.this[0].id : var.network_id
  subnet_id  = var.create_network ? vngcloud_vserver_subnet.this[0].id : var.subnet_id

  # SSH key to attach: the created key's id, else the passthrough value (blank = none).
  ssh_key = var.create_ssh_key ? vngcloud_vserver_sshkey.this[0].id : var.ssh_key_name

  # Image: a directly-provided img-... (var.image_id) wins; else the name lookup.
  image_id = var.image_id != "" ? var.image_id : try(data.vngcloud_vserver_image.this[0].id, "")

  # Flavor zone: a directly-provided UUID (var.flavor_zone_id) wins; else the name lookup.
  flavor_zone_id = var.flavor_zone_id != "" ? var.flavor_zone_id : try(data.vngcloud_vserver_flavor_zone.this[0].id, "")

  # Root-disk volume type: a directly-provided vtype-... wins; else the name lookup.
  root_disk_type_id = var.root_disk_type_id != "" ? var.root_disk_type_id : try(data.vngcloud_vserver_volume_type.this[0].id, "")
}

# ---------------------------------------------------------------------------
# Optional: open inbound SSH (tcp/22) on the first attached security group.
# The VNG "Default" secgroup allows nothing inbound, so SSH times out without this.
# ---------------------------------------------------------------------------
resource "vngcloud_vserver_secgrouprule" "ssh" {
  count = var.open_ssh && length(var.security_group) > 0 ? 1 : 0

  project_id        = var.project_id
  security_group_id = var.security_group[0]
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "TCP"
  port_range_min    = 22
  port_range_max    = 22
  remote_ip_prefix  = var.ssh_ingress_cidr
  # VNG allows only alphanumerics + _ . @ - space here, and it must start with a letter.
  description = "SSH inbound tcp 22 managed by terraform"
}

# Extra intra-VPC inbound TCP rules on the Default secgroup (opens nothing by default). Used for
# ops ports between boxes, e.g. the Portainer agent on tcp/9001 reached from the Portainer box's
# private IP. Keep each CIDR tight (a /32). See var.extra_ingress.
resource "vngcloud_vserver_secgrouprule" "extra" {
  for_each = length(var.security_group) > 0 ? { for r in var.extra_ingress : "${r.port}-${r.cidr}" => r } : {}

  project_id        = var.project_id
  security_group_id = var.security_group[0]
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "TCP"
  port_range_min    = each.value.port
  port_range_max    = each.value.port
  remote_ip_prefix  = each.value.cidr
  description       = "intra VPC tcp ${each.value.port} managed by terraform"
}

# ---------------------------------------------------------------------------
# The API servers.
# ---------------------------------------------------------------------------
resource "vngcloud_vserver_server" "this" {
  for_each = var.servers

  project_id = var.project_id
  name       = "${var.name_prefix}-${coalesce(each.value.name, each.key)}"
  zone_id    = var.zone_id

  flavor_id         = data.vngcloud_vserver_flavor.this[each.key].id
  image_id          = local.image_id
  encryption_volume = var.encryption_volume

  root_disk_size    = each.value.root_disk_size
  root_disk_type_id = local.root_disk_type_id

  network_id = local.network_id
  subnet_id  = local.subnet_id

  # Optional args are omitted (null) when left blank so the provider applies its default.
  ssh_key = local.ssh_key != "" ? local.ssh_key : null
  # security_group is REQUIRED by the provider schema (passing null errors with
  # "Missing required argument"), so always pass the list — even when empty.
  security_group  = var.security_group
  attach_floating = var.attach_floating

  user_name               = var.user_name != "" ? var.user_name : null
  user_password           = var.user_password != "" ? var.user_password : null
  user_data               = var.user_data != "" ? var.user_data : null
  user_data_base64_encode = var.user_data != "" ? var.user_data_base64_encode : null

  action = var.action

  lifecycle {
    # CRLF-vs-LF churn in the overlay's user_data heredoc otherwise reads as a change and
    # FORCES a destroy+recreate of the running box for a no-op. user_data only applies at
    # first boot, so ignore post-creation drift.
    ignore_changes = [user_data]

    # The zone/catalog data sources return an EMPTY id on a no-match (rather than
    # erroring), which otherwise surfaces as a confusing "Missing required
    # argument" on flavor_id/image_id/root_disk_type_id. Fail early, actionably.
    precondition {
      condition     = try(data.vngcloud_vserver_flavor.this[each.key].id, "") != ""
      error_message = "No flavor matched flavor_name=\"${each.value.flavor_name}\" in flavor_zone=\"${var.flavor_zone_name}\" (project ${var.project_id}). Copy the exact flavor + family name from the console create form."
    }
    precondition {
      condition     = local.image_id != ""
      error_message = "No image resolved: set var.image_id to a direct img-... (recommended — the name lookup requires an image associated with flavor_zone \"${var.flavor_zone_name}\", which the OS images are not for s2-general), or fix image_name."
    }
    precondition {
      condition     = local.root_disk_type_id != ""
      error_message = "No volume type resolved: set var.root_disk_type_id to a direct vtype-... (the volume_type_zone name lookup returns the DEFAULT AZ, often disabled), or fix root_disk_type_name/volume_type_zone_name."
    }
    precondition {
      condition     = var.create_network || (var.network_id != "" && var.subnet_id != "")
      error_message = "No network: set create_network=true to create a VPC+subnet, or provide BOTH network_id (net-...) and subnet_id (sub-...)."
    }
    precondition {
      condition     = var.project_id != "" && var.project_id != "pro-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
      error_message = "project_id must be your REAL VNG Cloud project id (pro-...), not the placeholder. Find it in the console project selector / overview."
    }
    precondition {
      condition     = !var.create_ssh_key || var.ssh_public_key != ""
      error_message = "create_ssh_key=true requires ssh_public_key (the OpenSSH public key material)."
    }
  }
}
