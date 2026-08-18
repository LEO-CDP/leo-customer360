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
# Project (always required: the vLB packages data source AND the LB resource
# both need a real project id; unlike vDB there is no create-network-only case)
# ---------------------------------------------------------------------------
variable "project_id" {
  type        = string
  description = "VNG Cloud project id (pro-...). Must be the project your credentials use. Find it in the console project selector / overview."
}

# ---------------------------------------------------------------------------
# Load balancer instance
# ---------------------------------------------------------------------------
variable "lb_name" {
  type        = string
  default     = "leo-customer360-nlb"
  description = "Name of the load balancer as shown in the console."
}

variable "package_name" {
  type        = string
  default     = "NLB_Small"
  description = "Package name. Used only when package_id is empty. NOTE: the data source returns the DEFAULT AZ's packages, whose uuids the create API rejects in other zones — prefer package_id."
}

variable "package_id" {
  type        = string
  default     = ""
  description = "Direct LB package uuid (lbp-...). When set, bypasses the package_name lookup — REQUIRED because vLB packages are per-AZ and the data source returns the DEFAULT AZ's, not yours. Get your zone's uuid from: GET {vlb_base_url}/v2/{project}/loadBalancers/packages?zoneId=<AZ>."
}

variable "lb_type" {
  type        = string
  default     = "Layer 4"
  description = "Load balancer type. 'Layer 4' = Network Load Balancer (NLB); 'Layer 7' = Application Load Balancer (ALB)."
  validation {
    condition     = contains(["Layer 4", "Layer 7"], var.lb_type)
    error_message = "lb_type must be exactly \"Layer 4\" (NLB) or \"Layer 7\" (ALB)."
  }
}

variable "scheme" {
  type        = string
  default     = "Internet"
  description = "LB scheme: 'Internet' (public, gets a public IP) or 'Internal' (private-only, reachable within the VPC)."
  validation {
    condition     = contains(["Internet", "Internal"], var.scheme)
    error_message = "scheme must be exactly \"Internet\" or \"Internal\"."
  }
}

variable "subnet_id" {
  type        = string
  default     = ""
  description = "Existing subnet id (sub-...) the LB lives in. Used when create_network = false; make this the SAME subnet as the backends the LB will front."
}

# --- Network creation (optional; set create_network = true to provision) ---
variable "create_network" {
  type        = bool
  default     = false
  description = "If true, create a VPC + subnet and place the LB in it; if false, use var.subnet_id. Normally false — the LB should share the backends' subnet, not get an isolated one."
}

variable "network_name" {
  type    = string
  default = "c360-lb-vpc"
}

variable "network_cidr" {
  type        = string
  default     = "10.100.0.0/16"
  description = "VPC CIDR (/16; within 10.0.0.0-10.255.0.0, 172.16-172.24, or 192.168.0.0)."
}

variable "subnet_name" {
  type    = string
  default = "c360-lb-subnet"
}

variable "subnet_cidr" {
  type        = string
  default     = "10.100.1.0/24"
  description = "Subnet CIDR; must be contained within network_cidr."
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
# Backends to expose: each entry becomes one pool + one listener (+ a secgroup
# rule opening the app port). Layer-4 NLB, so pools/listeners are TCP.
# ---------------------------------------------------------------------------
variable "backends" {
  type = map(object({
    member_ip   = string           # backend server's PRIVATE ip (must be in the LB subnet)
    member_port = number           # port the app listens on, on the backend
    listen_port = number           # public port exposed on the LB
    health_path = optional(string) # HTTP health-check path (e.g. /health); null -> plain TCP health check
  }))
  default     = {}
  description = "Services to expose through the LB: key -> { member_ip, member_port, listen_port, health_path }."
}

variable "backend_security_group_id" {
  type        = string
  default     = ""
  description = "Security group id (secg-...) on the backend servers. A rule per backend opens its member_port so the LB can reach it. Empty = skip (open the ports yourself)."
}

variable "backend_ingress_cidr" {
  type        = string
  default     = "0.0.0.0/0"
  description = "Source CIDR allowed to the backend app ports. An L4 NLB may PRESERVE the client source IP, so 0.0.0.0/0 is the safe default; tighten to the subnet CIDR only if VNG SNATs traffic to the LB."
}
