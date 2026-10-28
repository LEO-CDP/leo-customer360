# PROD overlay — customer360-promotions on the VKS Kubernetes cluster.
#
# NOT DEPLOYABLE YET: ../vks/overlays/prod.tfvars is still cluster_id = "CHANGE_ME", so
# there is no prod cluster. Prod ad serving stays on the dedicated `ads` vServer until one
# exists; this file (and k8s/overlays/prod/) are committed so the cutover is a deploy.
#
# POD COUNTS ARE NOT HERE — see k8s/overlays/prod/kustomization.yaml (2-8 pods).

promotions_port       = 9009
promotions_root_path  = ""           # prod serves at the root (no /ads path prefix), unlike uat
promotions_db_schema  = "leo_ads"
promotions_environment = "production"

# Per-POD SQLAlchemy pool. At the 8-pod prod ceiling the app defaults (10 + 20) would be
# 240 connections against the shared managed vDB from this service alone.
promotions_db_pool_size    = 5
promotions_db_max_overflow = 10
