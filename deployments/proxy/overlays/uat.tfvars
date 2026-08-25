# ── proxy (Caddy) · UAT overlay ────────────────────────────────────────────
# Caddy runs on the SHARED api box and reverse-proxies ONE public host to the
# co-located containers (127.0.0.1) + the backend box (dagster). It terminates
# TLS (auto Let's Encrypt), so this is the single HTTPS front door.
#
# CUTOVER PREREQS (see README): DNS  beta.leocdp.com -> the LB public IP, and the
# LB must forward :80 AND :443 to this box.

caddy_server_key = "api"              # which ../server key the container lands on (uat = shared api box)
caddy_domain     = "beta.leocdp.com"  # the public host; issuer/redirects derive from this
acme_email       = "admin@leocdp.com" # Let's Encrypt account email — CHANGE to a real inbox you monitor
caddy_image      = "caddy:2-alpine"

# Upstreams — all co-located on the api box (127.0.0.1) except dagster (backend box).
api_upstream       = "127.0.0.1:8008"  # customer360-api (root_path /c360api; Caddy strips /c360api)
keycloak_upstream  = "127.0.0.1:8080"  # Keycloak (serve under /auth -> set KC_HTTP_RELATIVE_PATH=/auth)
frontend_upstream  = "127.0.0.1:8890"  # frontend-admin (catch-all "/")
ads_upstream       = "127.0.0.1:9009"  # ads-server (/ads)
dagster_upstream   = "10.100.1.4:3000" # backend box (only if you enable the /dagster block)
netdata_upstream   = "127.0.0.1:4199"  # oauth2-proxy -> Netdata (only if you enable /netdata)
portainer_upstream = "127.0.0.1:9443"  # Portainer HTTPS (only if you enable /portainer)

# Jaeger trace UI served under /jaeger on :443 (TLS) via its oauth2-proxy SSO gate.
jaeger_upstream = "127.0.0.1:4686"

# data-tracking-api served under /data. On its OWN box (server key "tracking"), so this is a
# PRIVATE cross-box ip, NOT 127.0.0.1. Assumes DHCP gives the tracking box 10.100.1.8 — verify
# with `cd ../server && terraform output servers` and correct if it differs.
data_upstream = "10.100.1.8:8010"

# redis-commander (broker viewer) served under /redis on :443 (TLS) with its own basic-auth login.
# Same tracking box; the container runs URL_PREFIX=/redis so the prefix is forwarded unstripped.
redis_upstream = "10.100.1.8:8081"
