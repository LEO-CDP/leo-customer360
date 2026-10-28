# --- Auth (vIAM service account) — same values as ../server/terraform.tfvars ---
variable "client_id" {
  description = "vIAM service-account client id."
  type        = string
  sensitive   = true
}

variable "client_secret" {
  description = "vIAM service-account client secret."
  type        = string
  sensitive   = true
}

# --- API endpoints ---
variable "token_url" {
  description = "vIAM token endpoint."
  type        = string
  default     = "https://iamapis.vngcloud.vn/accounts-api/v2/auth/token"
}

variable "vserver_base_url" {
  description = "vServer gateway (the VKS API is reached through the same region)."
  type        = string
  default     = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
}

# --- Target cluster ---
variable "cluster_id" {
  description = "Existing VKS cluster id (created in the console). Per-env in overlays/."
  type        = string
}

# --- Node group shape ---
variable "node_group_name" {
  description = "Name of the worker node group."
  type        = string
  default     = "workers"

  # The API rejects a bad name only once the node group is being CREATED — i.e.
  # after any other resources in the plan have already been made. Enforcing it here
  # turns that into a plan-time error. Rule, verbatim from the API 400:
  # "A node group name must be from 5 to 15 characters. It must contain alphanumeric
  #  characters and hyphens. The alpha characters can only be lowercase. It must
  #  begin and end with a letter or number."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{3,13}[a-z0-9]$", var.node_group_name))
    error_message = "node_group_name must be 5-15 chars, lowercase letters/digits/hyphens, starting and ending with a letter or digit."
  }
}

variable "num_nodes" {
  description = <<-EOT
    Worker count. The floor is driven by what must fit: the cluster's own system
    pods (cilium, coredns x2, CSI controller, the vngcloud load-balancer
    controller), KEDA, the bundled Prometheus, and customer360-event-api's 3
    replicas — plus headroom for the HPA to scale those out.
  EOT
  type        = number
  default     = 2
}

# The two opaque ids below have NO Terraform data source in the vngcloud provider,
# so they cannot be looked up by name — copy them from the VKS console.
# (image_id is deliberately not among them: the provider derives the node image and
# ignores a configured value — see the note in main.tf.)
variable "flavor_id" {
  description = "Worker flavor id (vCPU/RAM tier). From the VKS console."
  type        = string
}

variable "ssh_public_key" {
  description = <<-EOT
    Public key to register when ssh_key_id is null. Reuse the SAME key as the
    vServers (../server/terraform.tfvars ssh_public_key) so one private key opens
    both the VMs and the Kubernetes nodes.
  EOT
  type        = string
  default     = null
}

variable "ssh_key_name" {
  description = "Name for the registered key. Null derives it from the node group name."
  type        = string
  default     = null
}

variable "project_id" {
  description = "vServer project id that owns the SSH key (same as ../server)."
  type        = string
}

variable "subnet_id" {
  description = <<-EOT
    Subnet the worker nodes join. This is load-bearing, not cosmetic: the pods must
    reach the api box's PRIVATE Redis at 10.100.1.5:6580 (the Redis Streams queue —
    without it customer360-event-api returns 503) and Jaeger on :4318. Pick a subnet
    in the SAME VPC as the vServers from ../server, or no security-group rule will
    make that hop work.
  EOT
  type        = string
}

variable "disk_size" {
  description = "Worker root disk in GB."
  type        = number
  default     = 40
}

variable "enable_private_nodes" {
  description = <<-EOT
    Private (no public IP) workers. Keep false unless the VPC has a NAT gateway:
    the pods need egress to pull the image from ghcr.io and to write to vStorage
    (https://hcm04.vstorage.vngcloud.vn). Private nodes without NAT will
    ImagePullBackOff and fail every S3 write.
  EOT
  type        = bool
  default     = false
}

