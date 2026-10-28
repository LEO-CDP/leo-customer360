# UAT overlay — customer360-promotions runs as a Deployment on the VKS Kubernetes cluster.
# Read by deploy-promotions.sh (grep); no Terraform. No secrets (DB/Redis creds come from
# ../postgres and ../cache).
#
# POD COUNTS ARE NOT HERE. They live in k8s/overlays/uat/kustomization.yaml, so a capacity
# change is a reviewable diff against the manifest that actually carries it. This file
# holds the application's configuration.

promotions_port       = 9009
promotions_root_path  = "/ads"       # public mount behind Caddy (beta.leocdp.com/ads); keeps Swagger/openapi + redirects prefixed
promotions_db_schema  = "leo_ads"    # customer360-promotions schema in the customer360 DB (no RLS)
promotions_environment = "production"

# Per-POD SQLAlchemy pool. The total connection count against the shared managed vDB is
# (pool_size + max_overflow) x replicas, and the HPA moves that replica count. At 5+10
# the 2-pod UAT ceiling caps this service at 30 connections; the app defaults (10 + 20)
# would make it 60.
promotions_db_pool_size    = 5
promotions_db_max_overflow = 10

# Persist OpenTelemetry tracing ON for uat across redeploys (read by deploy-promotions.sh -> lib/otel.sh;
# an explicit OTEL_ENABLED env var still overrides). Default without this is OFF on uat.
otel_enabled = "true"
