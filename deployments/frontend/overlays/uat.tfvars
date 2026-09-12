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

# Persist OpenTelemetry tracing ON for uat across redeploys (read by deploy-frontend.sh ->
# lib/otel.sh; an explicit OTEL_ENABLED env var still overrides). Default without this is OFF on uat.
otel_enabled = "true"

# --- Docs Assistant (chatbot) -> docs-vector-search on the "docs" box -------------------
# The browser calls the same-origin /ai/* proxy in frontend-admin, which forwards SERVER-SIDE
# to the docs box over the PRIVATE network (no CORS; the docs box stays unexposed). Leave
# docs_search_url EMPTY to auto-resolve the "docs" server's private fixed_ip:docs_search_port
# from ../server outputs; set it explicitly only to override. NOTE: the api box (10.100.1.5,
# where the frontend runs) must be allowed to reach docs:8001 — see the extra_ingress rule in
# ../server/overlays/uat.tfvars (apply out-of-band with ./deploy.sh uat apply).
docs_search_url        = ""      # empty -> auto-resolve from the "docs" box private IP
docs_search_server_key = "docs"  # server key of the docs-vector-search box
docs_search_port       = 8001
# Where the chatbot links its citations (the public docs site on GitHub Pages).
docs_site_base         = "https://leo-cdp.github.io/leo-customer360"
