# PROD overlay — ads-server runs on a DEDICATED vServer (server key "ads",
# defined in ../server/overlays/prod.tfvars). Ad-serving can be high-QPS, so it
# gets its own box in prod rather than sharing the api box's single vCPU.

ads_server_key = "ads"        # dedicated vServer (c360-api-prod-ads)
ads_port       = 9009
ads_db_schema  = "leo_ads"
ads_environment = "production"
ads_seed_sample = false       # don't load demo data into prod
