# UAT overlay — customer360-backend (Dagster) runs as a Deployment on the VKS cluster.
# Read by deploy-backend-vks.sh (grep); no Terraform. No secrets (DB/S3/Redis creds come
# from ../postgres, ../storage, ../cache).
#
# Resource sizing lives in k8s/overlays/uat/kustomization.yaml, not here.

dagster_port = 3000
# Dedicated Dagster metadata DB on the shared managed Postgres (created best-effort by the
# image's renderer, and by deploy-backend-vks.sh — a managed-PG app user often cannot CREATE DATABASE).
dagster_pg_db = "dagster"
# Global cap on concurrent runs. Each run opens its own DB connections against the shared vDB.
dagster_max_concurrent_runs = 10
# Fail the pod instead of silently falling back to local compute logs when vStorage is down.
dagster_require_s3 = "true"
# image_tag = "sha-<40-char-sha>"   # CI publishes sha-<long sha> and latest; IMAGE_TAG env wins

# vServer parity: the backend box is s-general-4x8 (../server/overlays/uat.tfvars), raised from
# 2x4 after code-server heartbeat deaths and run-worker OOM. A VKS node must offer at least this
# much ALLOCATABLE (a 4x8 node ~ 3.8 CPU / 6.3 GiB after kubelet reservation). check-capacity.sh enforces it.
dagster_min_node_cpu_m  = 3500
dagster_min_node_mem_mi = 6000
