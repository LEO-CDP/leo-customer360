# UAT overlay — frontend-admin runs as a Docker container on the API box.
# Read by deploy-frontend.sh (grep); no Terraform. No secrets here.

frontend_server_key = "api"    # SHARE c360-api-uat-api (10.100.1.5): the browser-facing box
                               # (api + keycloak) with headroom; the backend box (Dagster) is full.
frontend_port       = 8890

# The PUBLIC API URL the BROWSER calls (injected into the page as api_base = <host>/api/v1).
# Must be reachable from the client -> the LB, not localhost.
frontend_api_hostname = "https://beta.leocdp.com/c360api"

# Empty = serve at the LB root. The app mounts static at /static, but the template's
# static_base is FRONTEND_ROOT_PATH/static — so a non-empty prefix only works behind an
# L7 proxy that strips it. Direct on the L4 LB, keep this empty.
frontend_root_path = ""

frontend_tenant_id = "11111111-1111-1111-1111-111111111111"
sso_login          = true      # match customer360-api (SSO_LOGIN=true)
