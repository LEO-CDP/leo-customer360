# ── proxy (Caddy) · PROD overlay ───────────────────────────────────────────
# PROD runs each service on a DEDICATED box, so Caddy cannot use 127.0.0.1 for
# them — set each upstream to that box's PRIVATE IP (read from ../server outputs
# once prod is provisioned). Caddy itself co-locates on the frontend box (public
# entry) by default; give it its own box by pointing caddy_server_key elsewhere.

caddy_server_key = "frontend"           # prod: run Caddy on the frontend box (or a dedicated "proxy" key)
caddy_domain     = "leocdp.com"         # prod public host — CHANGE as needed (e.g. app.leocdp.com)
acme_email       = "admin@leocdp.com"   # Let's Encrypt account email — CHANGE to a real monitored inbox
caddy_image      = "caddy:2-alpine"

# Cross-box PRIVATE IPs — FILL from ../server (prod) outputs; these are placeholders.
api_upstream       = "10.101.1.10:8008" # api box
keycloak_upstream  = "10.101.1.11:8080" # sso box (KC_HTTP_RELATIVE_PATH=/auth)
frontend_upstream  = "10.101.1.12:8890" # frontend box (same box Caddy runs on -> could be 127.0.0.1)
ads_upstream       = "10.101.1.13:9009" # ads box
dagster_upstream   = "10.101.1.14:3000" # backend box
netdata_upstream   = "127.0.0.1:4199"   # monitoring (adjust to where oauth2-proxy runs in prod)
portainer_upstream = "127.0.0.1:9443"   # monitoring
