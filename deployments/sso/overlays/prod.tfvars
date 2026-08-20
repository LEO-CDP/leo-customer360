# PROD overlay — Keycloak runs on a DEDICATED vServer (not shared with the API).
# The dedicated box is defined in ../server/overlays/prod.tfvars under server key "sso".
# Read by deploy-sso.sh (grep); no Terraform here.

sso_server_key      = "sso"                    # DEDICATED vServer (c360-api-prod-sso)
keycloak_image      = "keycloak/keycloak:26.7"
keycloak_command    = "start"                  # production mode: HTTP behind the LB (TLS terminates at the LB)
keycloak_http_port  = 8080
keycloak_admin_user = "admin"

# REQUIRED in production `start` mode: the public URL clients reach Keycloak at
# (the LB / DNS name). Set this before deploying prod.
keycloak_hostname = "sso.example.com" # TODO: real public Keycloak hostname

keycloak_db_name = "db_keycloak"

# Dedicated box -> let Keycloak size its own heap (leave empty).
java_heap = ""
