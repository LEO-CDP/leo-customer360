# PROD overlay — frontend-admin runs on a DEDICATED vServer (server key "frontend",
# defined in ../server/overlays/prod.tfvars). Read by deploy-frontend.sh; no secrets.

frontend_server_key = "frontend" # dedicated public web-tier box (c360-api-prod-frontend)
frontend_port       = 8890

# TODO: the prod PUBLIC API URL (prod LB address). The browser uses it.
frontend_api_hostname = "http://PROD_LB_ADDRESS:80"

frontend_root_path = ""
frontend_tenant_id = "11111111-1111-1111-1111-111111111111"
sso_login          = true
