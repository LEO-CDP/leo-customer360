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
  name       = var.flavor_zone_name
  project_id = var.project_id
}

data "vngcloud_vserver_volume_type_zone" "this" {
  name       = var.volume_type_zone_name
  project_id = var.project_id
}

# OS image (shared by every server — all Ubuntu Server 24.04).
data "vngcloud_vserver_image" "this" {
  name           = var.image_name
  project_id     = var.project_id
  flavor_zone_id = data.vngcloud_vserver_flavor_zone.this.id
}

# Root-disk volume type (shared — all SSD).
data "vngcloud_vserver_volume_type" "this" {
  name                = var.root_disk_type_name
  project_id          = var.project_id
  volume_type_zone_id = data.vngcloud_vserver_volume_type_zone.this.id
}

# Per-server compute flavor (CPU/RAM tier), one lookup per servers-map entry.
data "vngcloud_vserver_flavor" "this" {
  for_each = var.servers

  name           = each.value.flavor_name
  project_id     = var.project_id
  flavor_zone_id = data.vngcloud_vserver_flavor_zone.this.id
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
}

# ---------------------------------------------------------------------------
# The API servers.
# ---------------------------------------------------------------------------
resource "vngcloud_vserver_server" "this" {
  for_each = var.servers

  project_id = var.project_id
  name       = "${var.name_prefix}-${each.key}"
  zone_id    = var.zone_id

  flavor_id         = data.vngcloud_vserver_flavor.this[each.key].id
  image_id          = data.vngcloud_vserver_image.this.id
  encryption_volume = var.encryption_volume

  root_disk_size    = each.value.root_disk_size
  root_disk_type_id = data.vngcloud_vserver_volume_type.this.id

  network_id = local.network_id
  subnet_id  = local.subnet_id

  # Optional args are omitted (null) when left blank so the provider applies its default.
  ssh_key        = local.ssh_key != "" ? local.ssh_key : null
  security_group = length(var.security_group) > 0 ? var.security_group : null

  user_name               = var.user_name != "" ? var.user_name : null
  user_password           = var.user_password != "" ? var.user_password : null
  user_data               = var.user_data != "" ? var.user_data : null
  user_data_base64_encode = var.user_data != "" ? var.user_data_base64_encode : null

  action = var.action

  lifecycle {
    # The zone/catalog data sources return an EMPTY id on a no-match (rather than
    # erroring), which otherwise surfaces as a confusing "Missing required
    # argument" on flavor_id/image_id/root_disk_type_id. Fail early, actionably.
    precondition {
      condition     = try(data.vngcloud_vserver_flavor.this[each.key].id, "") != ""
      error_message = "No flavor matched flavor_name=\"${each.value.flavor_name}\" in flavor_zone=\"${var.flavor_zone_name}\" (project ${var.project_id}). Copy the exact flavor + family name from the console create form."
    }
    precondition {
      condition     = try(data.vngcloud_vserver_image.this.id, "") != ""
      error_message = "No image matched image_name=\"${var.image_name}\" for flavor_zone=\"${var.flavor_zone_name}\". Copy the exact Ubuntu 24.04 image name from the console image picker."
    }
    precondition {
      condition     = try(data.vngcloud_vserver_volume_type.this.id, "") != ""
      error_message = "No volume type matched root_disk_type_name=\"${var.root_disk_type_name}\" in volume_type_zone=\"${var.volume_type_zone_name}\". Copy the exact type from the console."
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
