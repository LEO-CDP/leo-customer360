# proxy — Caddy (TLS + path routing)

A single **Caddy** reverse proxy that terminates TLS (auto **Let's Encrypt**) and
path-routes **one public host** (`caddy_domain`, e.g. `beta.leocdp.com`) to the
platform services. It replaces the "one raw port per service" scheme of the L4 NLB
with a clean HTTPS front door — at **$0 extra cost**, since it runs as a container on
a box you already pay for (the shared api box in uat).

```
Client ──HTTPS──▶ LB :443 ─(TCP passthrough)─▶ Caddy :443 (api box)
                  LB :80  ─(ACME + redirect)─▶ Caddy :80
                                                  │  terminates TLS, routes by path:
                                                  ├─ /            → frontend-admin :8890
                                                  ├─ /c360api/*    → customer360-api :8008   (prefix stripped)
                                                  ├─ /auth/*       → keycloak :8080          (KC serves under /auth)
                                                  ├─ /ads/*        → ads-server :9009        (prefix stripped)
                                                  └─ /cdp-sdk/*    → data-tracking-api :8010 (iframe + assets)
```

The L4 NLB stays in front (it's the thing with the public IP), but for the app it now
does dumb TCP passthrough of `:80`/`:443` to Caddy. Ops dashboards (Dagster, Portainer,
Netdata) can stay on their existing LB ports, or move under Caddy later (see Caveats).

## Files

| File | Purpose |
|------|---------|
| `Caddyfile` | routing + TLS config; `{$VAR}` placeholders resolved from env at load |
| `deploy-caddy.sh` | ships the Caddyfile + runs Caddy on the target box over SSH |
| `overlays/uat.tfvars` · `overlays/prod.tfvars` | per-env host, ACME email, upstreams |

## Usage

```bash
./deploy-caddy.sh uat validate   # adapt+validate the Caddyfile on the box (no deploy, no ACME)
./deploy-caddy.sh uat            # (re)deploy Caddy; issues/renews the cert once DNS+LB are ready
./deploy-caddy.sh uat destroy    # remove the container (cert volume caddy_data is kept)
```

> **Certs need three things** before Let's Encrypt will issue: (1) `caddy_domain`
> resolves (DNS A record) to the LB public IP, (2) the LB forwards **:80** to this box
> (HTTP-01 challenge) and **:443** for traffic, (3) Caddy is running. Until all three
> hold, Caddy runs but serves no HTTPS — that's expected while staging.

### Web SDK iframe

The hidden web SDK iframe is served by `data-tracking-api` at
`/cdp-sdk/html/cdp-event-proxy.html`. The route must remain before the frontend
catch-all in [`Caddyfile`](./Caddyfile). Caddy removes any upstream
`X-Frame-Options` header and sends `Content-Security-Policy` with the parent
origin configured by `sdk_frame_ancestor` in the environment overlay.

`X-Frame-Options: SAMEORIGIN` is not sufficient when the embedding page and the
iframe use different origins. Set `sdk_frame_ancestor` to the exact HTTPS origin
of the embedding site, then redeploy Caddy.

The current environment mapping is `https://beta.leocdp.com` for UAT and
`https://c360.leocdp.com` for production.

---

## Cutover runbook: put the platform behind `beta.leocdp.com`

Everything below is the **cutover** — do it once DNS is ready. Until you start, the
current `http://103.245.254.29:<port>` access keeps working, so you can prep the edits
first and flip in one sitting. Order matters (Keycloak's issuer changes, which
invalidates existing tokens — everyone re-logs-in once).

### 0. Prereqs
All services already deployed and healthy on the IP (api, keycloak, frontend, ads, LB).

### 1. DNS
Create an **A record**: `beta.leocdp.com → 103.245.254.29` (the LB public IP). Confirm
it resolves before continuing (`nslookup beta.leocdp.com`).

### 2. Edit the overlays (host + scheme; nothing applied yet)

**Fast path** — apply the ready-made patch instead of hand-editing all four files:
```bash
git apply deployments/proxy/cutover-beta.leocdp.com.patch   # from the repo root
git diff --stat                                             # review before redeploying
```
It makes exactly the edits below (LB `backends` map + the sso/frontend/monitoring hosts).
To undo: `git apply -R deployments/proxy/cutover-beta.leocdp.com.patch`.

| File | Key | From | To |
|------|-----|------|----|
| `sso/overlays/uat.tfvars` | `keycloak_hostname` | `http://103.245.254.29:8080` | `https://beta.leocdp.com` |
| `sso/overlays/uat.tfvars` | *(add)* `keycloak_http_relative_path` | — | `/auth` |
| `sso/overlays/uat.tfvars` | *(add)* `keycloak_proxy_headers` | — | `xforwarded` |
| `sso/overlays/uat.tfvars` | `api_sso_login_url` | `http://103.245.254.29:8080` | `https://beta.leocdp.com/auth` |
| `sso/overlays/uat.tfvars` | `sso_redirect_uris` | `http://103.245.254.29:8890/*` | `https://beta.leocdp.com/*` |
| `frontend/overlays/uat.tfvars` | `frontend_api_hostname` | `http://103.245.254.29:80` | `https://beta.leocdp.com/c360api` |
| `monitoring/overlays/uat.tfvars` | `oauth2_issuer_url` | `http://103.245.254.29:8080/realms/customer360` | `https://beta.leocdp.com/auth/realms/customer360` |
| `monitoring/overlays/uat.tfvars` | `oauth2_public_host` | `103.245.254.29` | `beta.leocdp.com` |

> **Why `/auth` everywhere:** with `keycloak_http_relative_path=/auth`, Keycloak's OIDC
> **issuer** becomes `https://beta.leocdp.com/auth/realms/customer360`. Every consumer
> (API introspection, oauth2-proxy discovery, the browser's authorize redirect) must use
> the *same* issuer string, or introspection/validation fails.

> **Ordering matters.** The OIDC consumers (oauth2-proxy, the API) do discovery against
> the issuer **at startup** — so they get redeployed *after* Caddy + the LB make
> `https://beta.leocdp.com/auth` live (steps 4–6), never before. Doing it earlier
> crash-loops oauth2-proxy on an unreachable issuer.

### 3. Redeploy Keycloak (new hostname + `/auth` + proxy-trust)
```bash
(cd sso && ./deploy-sso.sh uat)
```
Sets `KC_HOSTNAME=https://beta.leocdp.com`, `KC_HTTP_RELATIVE_PATH=/auth`,
`KC_PROXY_HEADERS=xforwarded`, `KC_HOSTNAME_STRICT=false` so Keycloak builds correct
`https://…/auth/…` URLs behind Caddy. (Keycloak itself doesn't need the domain reachable
to start — it just stamps these into the URLs it generates.)

### 4. Deploy Caddy
```bash
(cd proxy && ./deploy-caddy.sh uat)
```

### 5. Repoint the LB: `:80` + `:443` → Caddy
The patch (step 2) already rewrote `load_balancer/overlays/uat.tfvars` — the
`api`/`keycloak`/`frontend`/`ads` backends are replaced by `caddy_http` (:80) +
`caddy_https` (:443), and the ops ports (dagster/portainer/netdata) stay. Apply it:
```bash
(cd load_balancer && ./deploy.sh uat apply)
```
The LB security-group rules auto-open `:80`/`:443` on the api box. **This is the moment
the domain goes live:** Caddy sees the ACME HTTP-01 challenge on `:80` and issues the
Let's Encrypt cert for `beta.leocdp.com` (watch `sudo docker logs c360-caddy`).

> **If apply errors `port 80 … in use`:** the old `api` listener (also `:80`) is being
> replaced by `caddy_http`, and the API can create-before-destroy. Just run
> `./deploy.sh uat apply` **again** — the first run frees `:80`, the second creates the
> Caddy listener cleanly. (`:443` is new, so it never conflicts.)

### 6. Confirm the issuer is live (gate before the OIDC consumers)
```bash
curl -s https://beta.leocdp.com/auth/realms/customer360/.well-known/openid-configuration | head -c 200
```
Must return JSON with `"issuer":"https://beta.leocdp.com/auth/realms/customer360"`. If it
404s or the TLS handshake fails, wait for Caddy to finish issuing before continuing.

### 7. Re-register clients + redeploy the OIDC consumers
```bash
(cd sso        && python3 bootstrap-realm.py)   # customer360-api redirect URIs -> https://beta.leocdp.com/*
(cd monitoring && ./deploy-monitoring.sh uat)   # re-provisions c360-oauth2-proxy client + new issuer
(cd server     && ./deploy-api.sh uat)          # SSO_LOGIN_URL=https://beta.leocdp.com/auth
(cd frontend   && ./deploy-frontend.sh uat)     # apiBase=https://beta.leocdp.com/c360api/api/v1
```

### 8. Verify end-to-end
```bash
curl -sI https://beta.leocdp.com/                       # frontend (200)
curl -s  https://beta.leocdp.com/c360api/api/v1/health  # api
curl -sSI https://beta.leocdp.com/cdp-sdk/html/cdp-event-proxy.html \
  | grep -Ei '^(HTTP/|content-security-policy:|x-frame-options:)'
```
Then log in from the UI at `https://beta.leocdp.com/` — the whole OIDC round-trip should
run over `https://beta.leocdp.com/auth`. Existing sessions are invalid (the issuer
changed), so everyone logs in once more.

### Rollback
Reverse the patch and re-deploy in the same order:
```bash
git apply -R deployments/proxy/cutover-beta.leocdp.com.patch
(cd sso && ./deploy-sso.sh uat)
(cd load_balancer && ./deploy.sh uat apply)     # restores the per-port backends
(cd server && ./deploy-api.sh uat) && (cd frontend && ./deploy-frontend.sh uat) && (cd monitoring && ./deploy-monitoring.sh uat)
```
Caddy can be left running or `./deploy-caddy.sh uat destroy`'d — it's harmless when the
LB no longer points at it.

---

## Caveats (single-host path routing)

- **Keycloak** must serve under `/auth` (`KC_HTTP_RELATIVE_PATH=/auth`) so its absolute
  URLs match the path — handled by the new `deploy-sso.sh` keys. Caddy forwards
  `/auth/*` **without** stripping.
- **customer360-api** has `root_path=/c360api`, so Caddy uses `handle_path` to **strip**
  `/c360api` and the app sees its own `/api/v1/...` routes; `root_path` keeps its
  generated URLs (docs) correct.
- **ads-server** is proxied under `/ads` with the prefix stripped — fine for its JSON
  API; if it serves UI assets it'd need its own base-path config.
- **Web SDK iframe** is proxied under `/cdp-sdk` without stripping the prefix because
  `data-tracking-api` mounts the SDK at that path. Its response allows only the origin
  configured by `sdk_frame_ancestor`; do not replace this with `SAMEORIGIN` for a
  cross-origin embedding site.
- **Dagster / Portainer / Netdata** generate absolute URLs or have their own callback
  paths and do **not** sub-path cleanly without app-side config (`dagster --path-prefix`,
  `oauth2-proxy --proxy-prefix`, Portainer base href). By default they stay on their raw
  LB ports; the Caddyfile has commented blocks + notes if you want to bring one under a
  path. A subdomain (`dagster.beta.leocdp.com`) is the cleaner route for these.
- **HTTPS knock-ons:** once TLS is live, set oauth2-proxy `--cookie-secure=true` and use
  `https://` callback URLs (the monitoring overlay/`deploy-monitoring.sh` drive this).

## prod

Each service runs on its **own box** in prod, so Caddy can't use `127.0.0.1` — set each
`*_upstream` in `overlays/prod.tfvars` to that box's **private IP** (from `server` prod
outputs), and pick where Caddy runs via `caddy_server_key` (default: the frontend box).
