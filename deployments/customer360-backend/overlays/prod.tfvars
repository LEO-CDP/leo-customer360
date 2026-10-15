# PROD overlay — see uat.tfvars. Not applied yet: the prod cluster/subnet ids are still
# placeholders in ../vks/overlays/prod.tfvars.
dagster_port = 3000
dagster_pg_db = "dagster"
dagster_max_concurrent_runs = 25
dagster_require_s3 = "true"
