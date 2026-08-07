variable "create_network" {
  type        = bool
  description = "Create a dedicated network + subnet. When false, nothing is created and the stack uses the existing network_id/subnet_id."
}

variable "project_id" {
  type        = string
  description = "VNG vServer project ID (pro-xxxx) that owns the network."
}

variable "name_prefix" {
  type        = string
  description = "Prefix applied to created resource names."
}

variable "zone_id" {
  type        = string
  description = "Availability zone (e.g. HCM03-1A)."
}

variable "network_cidr" {
  type        = string
  description = "Network CIDR /16 (allowed: 10.0.0.0-10.255.0.0, 172.16.0.0-172.24.0.0, 192.168.0.0)."
  default     = "10.76.0.0/16"
}

variable "subnet_cidr" {
  type        = string
  description = "Subnet CIDR, contained within network_cidr."
  default     = "10.76.1.0/24"
}
