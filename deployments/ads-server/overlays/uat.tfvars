# UAT overlay — ads-server runs as a Docker container on the API box (co-located).
# Read by deploy-ads.sh (grep); no Terraform. No secrets (DB/Redis creds come from
# ../postgres and ../cache).

ads_server_key = "api"        # SHARE c360-api-uat-api (10.100.1.5): reuses local Redis :6580
ads_port       = 9009
ads_root_path  = "/ads"       # public mount behind Caddy (beta.leocdp.com/ads); keeps Swagger/openapi + redirects prefixed
ads_db_schema  = "leo_ads"    # ad-server's own schema in the customer360 DB (no RLS)
ads_environment = "production"
ads_seed_sample = true        # load sql-scripts/sample-data-init.sql (demo advertisers/campaigns)
