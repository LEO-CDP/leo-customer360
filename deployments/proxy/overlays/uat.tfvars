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
frontend_upstream  = "127.0.0.1:8890"  # customer360-frontend (catch-all "/")
ads_upstream       = "127.0.0.1:9009"  # customer360-promotions (/ads)
dagster_upstream   = "10.100.1.4:3000" # backend box (only if you enable the /dagster block)
netdata_upstream   = "127.0.0.1:4199"  # oauth2-proxy -> Netdata (only if you enable /netdata)
portainer_upstream = "127.0.0.1:9443"  # Portainer HTTPS (only if you enable /portainer)

# Jaeger trace UI served under /jaeger on :443 (TLS) via its oauth2-proxy SSO gate.
jaeger_upstream = "127.0.0.1:4686"

# customer360-event-api served under /data — currently a 50/50 CANARY across both homes:
#   10.100.1.8:8010    the old tracking vServer (still running; the rollback target)
#   49.213.73.13:8010  the VKS `event-api` Service (type LoadBalancer)
#
# Splitting is safe for this workload specifically. Beacons are independent POSTs with no
# session state, both homes publish to the SAME Redis stream and the same vStorage buckets,
# and the Bronze object key is a uuid5 over the event ids — so concurrent writers cannot
# collide and a retry re-writes identical bytes to an identical key.
#
# To shift the split, change the weights (order matches the upstream order above):
#   "weighted_round_robin 9 1"  -> 10% to VKS
#   "weighted_round_robin 5 5"  -> 50/50
#   "weighted_round_robin 0 10" -> all VKS  (now)
# Caddy rejects the config if the weight count does not match the upstream count.
#
# The old box stays LISTED at weight 0 rather than being removed from data_upstream:
# a 0-weight upstream is never selected (verified with `caddy validate`), so it takes no
# traffic, but rolling back is then a one-value edit instead of re-adding an address.
# Drop it from data_upstream only when the vServer itself is destroyed.
data_upstream  = "10.100.1.8:8010 49.213.73.13:8010"
data_lb_policy = "weighted_round_robin 0 10"

# docs-vector-search served under /docs-ai (the public docs site's chatbot calls it cross-origin).
# On its OWN box (server key "docs"), so a PRIVATE cross-box ip. Reachable from the api box (Caddy)
# via the docs:8001 extra_ingress rule in ../server/overlays/uat.tfvars. Verify with
# `cd ../server && terraform output servers`.
docs_upstream = "10.100.1.7:8001"

# customer360-agent — ONLY /agent/health is routed (see Caddyfile); /plan/* stays internal.
# Its own box (server key "agent"); VERIFY the private IP with `terraform output servers`
# after ../server apply. Caddy runs on the api box, which already reaches the agent on :8009.
agent_upstream = "10.100.1.10:8009" # agent-box private ip (verified via hostname -I on the box)

# Parent origin allowed to embed the hidden c360 web SDK iframe.
sdk_frame_ancestor = "https://beta.leocdp.com"
