# ---------------------------------------------------------------------------
# Monthly cost estimate (VND) for the vStorage usage.
#
#   storage   = estimated_storage_tb   * price_storage_per_tb_vnd
#   bandwidth = estimated_bandwidth_gb * price_bandwidth_per_gb_vnd
#   total     = storage + bandwidth
#
# Pure arithmetic on the input variables — creates no resources. Surfaced via
# outputs.tf so `terraform plan` doubles as a quick bill review.
# ---------------------------------------------------------------------------
locals {
  est_storage_cost_vnd   = var.estimated_storage_tb * var.price_storage_per_tb_vnd
  est_bandwidth_cost_vnd = var.estimated_bandwidth_gb * var.price_bandwidth_per_gb_vnd
  est_total_monthly_vnd  = local.est_storage_cost_vnd + local.est_bandwidth_cost_vnd

  # Group the thousands with a comma so the printed VND figures stay readable
  # (Terraform's format() has no locale-aware thousands separator).
  cost_breakdown = {
    storage_tb         = var.estimated_storage_tb
    bandwidth_gb       = var.estimated_bandwidth_gb
    storage_cost_vnd   = local.est_storage_cost_vnd
    bandwidth_cost_vnd = local.est_bandwidth_cost_vnd
    total_month_vnd    = local.est_total_monthly_vnd
  }
}
