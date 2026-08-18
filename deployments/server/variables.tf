# ---------------------------------------------------------------------------
# Authentication (from GreenNode/VNG Cloud console -> IAM -> Service Account)
# ---------------------------------------------------------------------------
variable "client_id" {
  type        = string
  description = "vIAM service-account client id."
}

variable "client_secret" {
  type        = string
  description = "vIAM service-account client secret."
  sensitive   = true
}

# ---------------------------------------------------------------------------
# Platform endpoints (defaults target VNG Cloud, which GreenNode runs on)
# ---------------------------------------------------------------------------
variable "token_url" {
  type    = string
  default = "https://iamapis.vngcloud.vn/accounts-api/v2/auth/token"
}

variable "vserver_base_url" {
  type    = string
  default = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
}

variable "vlb_base_url" {
  type    = string
  default = "https://hcm-3.api.vngcloud.vn/vserver/vlb-gateway"
}

variable "vdb_base_url" {
  type    = string
  default = "https://vdb-gateway.vngcloud.vn"
}

# ---------------------------------------------------------------------------
# Project & zone (every vserver data source + the server resource need these)
# ---------------------------------------------------------------------------
variable "project_id" {
  type        = string
  description = "VNG Cloud project id (pro-...). Must be the project your credentials use. Find it in the console project selector / overview."
}

variable "zone_id" {
  type    = string
  default = "HCM03-1A"
  validation {
    condition     = contains(["HCM03-1A", "HCM03-1B", "HCM03-1C"], var.zone_id)
    error_message = "zone_id must be one of HCM03-1A, HCM03-1B, HCM03-1C."
  }
}

# ---------------------------------------------------------------------------
# Catalog lookups (name -> id). These names are ACCOUNT/ZONE-specific: copy the
# exact strings from the console create form. A wrong name fails the plan via a
# precondition in main.tf with an actionable message (see README "Reality check").
# ---------------------------------------------------------------------------
variable "flavor_zone_name" {
  type        = string
  default     = "General v2 Instances"
  description = "Flavor-zone display name. Used only when flavor_zone_id is empty. NOTE: many zones share a name, and the provider picks the FIRST match — prefer flavor_zone_id."
}

variable "flavor_zone_id" {
  type        = string
  default     = ""
  description = "Direct flavor-zone id (a UUID). When set, bypasses the flavor_zone_name lookup — required when zones share a display name across AZs. From discover-catalog.py: a flavor's `zoneId` is this UUID; its `flavorZoneId` field is the actual AZ (pick the AZ that isn't sold out)."
}

variable "volume_type_zone_name" {
  type        = string
  default     = "SSD"
  description = "Volume-type-zone display name the root-disk type lives under (e.g. SSD)."
}

variable "image_name" {
  type        = string
  default     = "Ubuntu 24.04 x64"
  description = "OS image name (matched on the image's imageVersion). Used only when image_id is empty."
}

variable "image_id" {
  type        = string
  default     = ""
  description = "Direct image id (img-...). When set, bypasses the image_name lookup — needed for s2-general flavors, whose flavor zone the OS images aren't associated with. Find via discover-catalog.py."
}

variable "root_disk_type_name" {
  type        = string
  default     = "SSD-IOPS3000"
  description = "Root-disk volume type name. Used only when root_disk_type_id is empty."
}

variable "root_disk_type_id" {
  type        = string
  default     = ""
  description = "Direct volume-type id (vtype-...). When set, bypasses the name lookup — required because the volume_type_zone name lookup returns the DEFAULT AZ's zone (often a disabled one, e.g. HCM03-1A), not your AZ's. From discover-catalog.py / the ?zoneId=<AZ> volume_type_zones query."
}

# ---------------------------------------------------------------------------
# Servers to provision (name suffix -> flavor + disk). for_each in main.tf.
# The default builds BOTH API-server sizes from the request; overlays override.
# ---------------------------------------------------------------------------
variable "name_prefix" {
  type        = string
  default     = "c360-api"
  description = "Prefix for each server name. Final name is name_prefix + \"-\" + the servers-map key."
}

variable "servers" {
  type = map(object({
    flavor_name    = string
    root_disk_size = number
    name           = optional(string) # server-name suffix; defaults to the map key
  }))
  default = {
    "4x8" = {
      flavor_name    = "s2-general-4x8" # 4 vCPU / 8 GB
      root_disk_size = 50
    }
    "8x16" = {
      flavor_name    = "s2-general-8x16" # 8 vCPU / 16 GB
      root_disk_size = 50
    }
  }
  description = "Map of API servers to create. Key is the name suffix; value sets the flavor (CPU/RAM tier) and root-disk size in GB."
}

variable "encryption_volume" {
  type        = bool
  default     = false
  description = "Encrypt the server's boot volume."
}

variable "action" {
  type        = string
  default     = "start"
  description = "Post-provision power state: start | stop | reboot."
  validation {
    condition     = contains(["start", "stop", "reboot"], var.action)
    error_message = "action must be one of start, stop, reboot."
  }
}

# ---------------------------------------------------------------------------
# Login: SSH key (recommended) and/or an admin user/password. Leave blank to
# omit. At least one auth method should be set so you can reach the box.
# ---------------------------------------------------------------------------
variable "create_ssh_key" {
  type        = bool
  default     = false
  description = "If true, register var.ssh_public_key as a new vServer SSH key and attach it to every server."
}

variable "ssh_key_name" {
  type        = string
  default     = ""
  description = "Name of the SSH key. When create_ssh_key=false this is an EXISTING key name/id to attach; when true it names the key to create."
}

variable "ssh_public_key" {
  type        = string
  default     = ""
  description = "OpenSSH public key material (ssh-rsa/ssh-ed25519 ...). Required when create_ssh_key = true."
}

variable "user_name" {
  type        = string
  default     = ""
  description = "Optional admin user to create on the instance."
}

variable "user_password" {
  type        = string
  default     = ""
  description = "Optional admin password. Do NOT commit a real value."
  sensitive   = true
  # VNG rule: >=1 lowercase, >=1 uppercase, >=1 digit, and one of * @ ! (RE2 has no
  # lookahead, so check each class separately).
  validation {
    condition = var.user_password == "" || (
      can(regex("[a-z]", var.user_password)) &&
      can(regex("[A-Z]", var.user_password)) &&
      can(regex("[0-9]", var.user_password)) &&
      can(regex("[@!*]", var.user_password))
    )
    error_message = "user_password must contain a lowercase, an uppercase, a digit, and one of * @ ! (VNG password rule)."
  }
}

variable "user_data" {
  type        = string
  default     = ""
  description = "Optional cloud-init / bootstrap script run on first boot."
}

variable "user_data_base64_encode" {
  type        = bool
  default     = false
  description = "Set true if user_data is already base64-encoded."
}

variable "security_group" {
  type        = list(string)
  default     = []
  description = "Existing security-group ids (secg-...) to attach. Empty = provider default."
}

variable "attach_floating" {
  type        = bool
  default     = false
  description = "Attach a floating (public) IP to each server — needed to SSH in from outside the VPC (e.g. a bastion)."
}

variable "open_ssh" {
  type        = bool
  default     = false
  description = "If true, add an inbound tcp/22 rule to the FIRST security_group so you can SSH in (the VNG Default secgroup opens nothing inbound)."
}

variable "ssh_ingress_cidr" {
  type        = string
  default     = "0.0.0.0/0"
  description = "CIDR allowed to reach tcp/22 when open_ssh = true. TIGHTEN to your public IP (e.g. 203.0.113.4/32) — 0.0.0.0/0 exposes SSH to the whole internet."
}

# ---------------------------------------------------------------------------
# Network: attach to an existing network+subnet, or create a fresh VPC+subnet.
# The server resource needs BOTH network_id and subnet_id.
# ---------------------------------------------------------------------------
variable "create_network" {
  type        = bool
  default     = false
  description = "If true, create a VPC + subnet and place the servers in it; if false, use var.network_id + var.subnet_id."
}

variable "network_id" {
  type        = string
  default     = ""
  description = "Existing network id (net-...). Used when create_network = false."
}

variable "subnet_id" {
  type        = string
  default     = ""
  description = "Existing subnet id (sub-...). Used when create_network = false."
}

variable "network_name" {
  type    = string
  default = "c360-api-vpc"
}

variable "network_cidr" {
  type        = string
  default     = "10.100.0.0/16"
  description = "VPC CIDR (/16; within 10.0.0.0-10.255.0.0, 172.16-172.24, or 192.168.0.0)."
}

variable "subnet_name" {
  type    = string
  default = "c360-api-subnet"
}

variable "subnet_cidr" {
  type        = string
  default     = "10.100.1.0/24"
  description = "Subnet CIDR; must be contained within network_cidr."
}
