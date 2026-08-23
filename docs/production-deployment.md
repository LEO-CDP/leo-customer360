# Customer 360 — Production Deployment with Two Domains

This guide describes how to run the Customer 360 platform in production when
you only have two public DNS domains available:

| Domain | Purpose |
|---|---|
| `cdp.example.com` | Both the **Customer 360 API** and the **frontend-admin** UI |
| `id.example.com` | **Keycloak** identity provider / SSO |

It builds on the existing [DOCKER-COMPOSE-GUIDE.md](DOCKER-COMPOSE-GUIDE.md) and
[devops-notes.md](devops-notes.md), but adapts them for the single-domain
API/frontend constraint.

---

## 1. Public URL plan

Because the API and frontend share `cdp.example.com`, route by **URL path**
on a reverse proxy:

| Public URL | Routed to | Internal container |
|---|---|---|
| `https://cdp.example.com/api/v1/*` | Customer 360 API | `customer360-api:8008` |
| `https://cdp.example.com/health` | API health check | `customer360-api:8008` |
| `https://cdp.example.com/docs` | API Swagger docs | `customer360-api:8008` |
| `https://cdp.example.com/openapi.json` | OpenAPI schema | `customer360-api:8008` |
| `https://cdp.example.com/*` | Admin frontend SPA | `customer360-frontend:8890` |
| `https://id.example.com/*` | Keycloak | `customer360-keycloak:8080` |

The frontend is a single-page app with a hash router (`/#/profiles`,
`/#/segments`, ...). Any path that is **not** an API/static asset should
return `index.html` so the browser can bootstrap the SPA.

Because both frontend and API are served from the **same origin**, the
browser no longer needs CORS for routine calls. The API's
`allow_origins=["*"]` middleware therefore becomes harmless but unnecessary;
you may restrict it later if desired.

---

## 2. What must change vs. the default compose stack

1. **Add `frontend-admin` to `docker-compose.yml`** — it is not part of the
   default production stack.
2. **Bind all host-published ports to `127.0.0.1`** and put Nginx in front.
3. **Set `FRONTEND_API_HOSTNAME` to a relative path** (`/api/v1`) so the
   browser calls the same origin.
4. **Run Keycloak in production mode** (`start`, not `start-dev`) with a
   proper hostname and reverse-proxy headers.
5. **Deploy an Nginx reverse proxy** that terminates TLS and routes paths.
6. **Configure the Keycloak `leocdp` client** with valid redirect/web origins
   pointing to `https://cdp.example.com`.

---

## 3. DNS and TLS prerequisites

### 3.1 DNS records

Point both domains at the public IP of the Docker host:

```text
cdp.example.com    A  <server-public-ip>
id.example.com     A  <server-public-ip>
```

### 3.2 TLS certificates

Obtain certificates for both domains. With Let's Encrypt + Certbot:

```bash
sudo apt update
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d cdp.example.com -d id.example.com
```

This will place certificates under:

```text
/etc/letsencrypt/live/cdp.example.com/fullchain.pem
/etc/letsencrypt/live/cdp.example.com/privkey.pem
/etc/letsencrypt/live/id.example.com/fullchain.pem
/etc/letsencrypt/live/id.example.com/privkey.pem
```

> Keep a cron job or systemd timer for `certbot renew`. Nginx must reload
> after renewal:
> `certbot renew --deploy-hook "systemctl reload nginx"`.

---

## 4. Add `frontend-admin` to `docker-compose.yml`

Append the service below to the existing
[`docker-compose.yml`](../docker-compose.yml). It reuses the same patterns as
`api` and `cir`.

```yaml
  # ---------------------------------------------------------------------------
  # 7) Customer 360 Admin Frontend (FastAPI static-site app).
  #    Serves index.html + static assets; injects FRONTEND_API_HOSTNAME into
  #    static/js/config.js at request time.
  # ---------------------------------------------------------------------------
  frontend:
    build:
      context: ./frontend-admin
      dockerfile: Dockerfile
    image: customer360-frontend:local
    container_name: customer360-frontend
    restart: unless-stopped
    init: true
    <<: *default-hardening
    env_file:
      - .env
    environment:
      # Empty hostname => browser uses same-origin /api/v1 paths.
      FRONTEND_API_HOSTNAME: ""
      FRONTEND_HOST_BIND: 0.0.0.0
      FRONTEND_HOST_PORT: 8890
    depends_on:
      api:
        condition: service_healthy
    ports:
      - "${FRONTEND_HOST_BIND:-127.0.0.1}:${FRONTEND_HOST_PORT:-8890}:8890"
    networks:
      - customer360-network
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 256m
    healthcheck:
      test:
        [
          "CMD",
          "python",
          "-c",
          "import urllib.request; urllib.request.urlopen('http://localhost:8890/health', timeout=3)",
        ]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 15s
```

Also update `manage-c360.sh` so the status command knows about the new
container:

```bash
DAGSTER_CONTAINER="customer360-dagster"
FRONTEND_CONTAINER="customer360-frontend"
ALL_CONTAINERS=("$POSTGRES_CONTAINER" "$REDIS_CONTAINER" "$KEYCLOAK_CONTAINER" "$DAGSTER_CONTAINER" "$API_CONTAINER" "$FRONTEND_CONTAINER")
```

---

## 5. `.env` configuration

Copy `.env.example` to `.env` and set the production values below. Values
not listed here follow the existing guides.

```dotenv
# ----- Public hostnames -----------------------------------------------------
KEYCLOAK_HOSTNAME=id.example.com
FRONTEND_API_HOSTNAME=

# ----- customer360-api / uvicorn --------------------------------------------
C360_API_HOST=0.0.0.0
C360_API_PORT=8008
C360_API_HOST=127.0.0.1
C360_API_PORT=8008

# ----- frontend-admin / uvicorn ---------------------------------------------
FRONTEND_HOST_BIND=0.0.0.0
FRONTEND_HOST_PORT=8890

# ----- Keycloak container (production mode) ---------------------------------
KEYCLOAK_COMMAND=start
# Introspection URL used by the API container (overridden to http://keycloak:8080
# inside the container by docker-compose.yml; this value is only relevant if you
# run the API directly on the host).
SSO_LOGIN_URL=https://id.example.com
KEYCLOAK_VERIFY_SSL=true

# Keycloak admin console credentials -- change from defaults.
KEYCLOAK_ADMIN=admin
KEYCLOAK_ADMIN_PASSWORD=<strong-random-password>

# Confidential client secret the API uses to introspect tokens.
KEYCLOAK_REALM=leocdp
KEYCLOAK_CLIENT_ID=leocdp
KEYCLOAK_CLIENT_SECRET=<strong-random-secret>

# Bind Keycloak to loopback; Nginx terminates TLS and proxies.
KEYCLOAK_HOST_PORT=8080
KEYCLOAK_HOST_BIND=127.0.0.1

# ----- Internal services (keep loopback-only) --------------------------------
POSTGRES_HOST_BIND=127.0.0.1
REDIS_HOST_BIND=127.0.0.1
```

### Important notes

- `FRONTEND_API_HOSTNAME=` (empty) makes `frontend-admin/app.py` render
  `apiBase: "/api/v1"`. The browser will call
  `https://cdp.example.com/api/v1/...`, which Nginx proxies to the API.
- `KEYCLOAK_COMMAND=start` switches Keycloak from `start-dev` to production
  mode. It **requires** TLS termination at Nginx and the hostname/proxy
  settings below.
- `KEYCLOAK_VERIFY_SSL=true` tells the API to validate the Keycloak TLS
  certificate during token introspection. Use valid certificates; self-signed
  certs will fail.

---

## 6. Keycloak service tweaks for production mode

Update the `keycloak` service block in `docker-compose.yml` to pass the
reverse-proxy settings:

```yaml
  keycloak:
    image: keycloak/keycloak:${KEYCLOAK_VERSION:-26.7}
    container_name: customer360-keycloak
    restart: unless-stopped
    init: true
    <<: *default-hardening
    environment:
      KC_DB: postgres
      KC_DB_URL: jdbc:postgresql://postgres:5432/db_keycloak
      KC_DB_USERNAME: ${DB_USER:-postgres}
      KC_DB_PASSWORD: ${DB_PASSWORD:?Set DB_PASSWORD in .env}
      KC_HOSTNAME: https://${KEYCLOAK_HOSTNAME:?Set KEYCLOAK_HOSTNAME in .env}
      KC_HOSTNAME_STRICT: "true"
      KC_PROXY_HEADERS: xforwarded
      KC_HTTP_ENABLED: "true"
      KC_HEALTH_ENABLED: "true"
      KEYCLOAK_ADMIN: ${KEYCLOAK_ADMIN:-admin}
      KEYCLOAK_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD:?Set KEYCLOAK_ADMIN_PASSWORD in .env}
    command: ["${KEYCLOAK_COMMAND:-start-dev}"]
    depends_on:
      postgres:
        condition: service_healthy
      keycloak-db-init:
        condition: service_completed_successfully
    ports:
      - "${KEYCLOAK_HOST_BIND:-127.0.0.1}:${KEYCLOAK_HOST_PORT:-8080}:8080"
    networks:
      - customer360-network
    deploy:
      resources:
        limits:
          cpus: "1.5"
          memory: 768m
    healthcheck:
      test: ["CMD-SHELL", "exec 3<>/dev/tcp/127.0.0.1/9000 && echo -e 'GET /health/ready HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n' >&3 && cat <&3 | grep -q '200'"]
      interval: 20s
      timeout: 10s
      retries: 10
      start_period: 60s
```

Keycloak will now advertise `https://id.example.com` in token issuer/URL
fields and trust the `X-Forwarded-*` headers from Nginx.

---

## 7. Nginx reverse proxy configuration

Create `/etc/nginx/sites-available/c360.conf`:

```nginx
# Upstreams (Docker host-published ports bound to 127.0.0.1)
upstream c360_api {
    server 127.0.0.1:8008;
}

upstream c360_frontend {
    server 127.0.0.1:8890;
}

upstream keycloak {
    server 127.0.0.1:8080;
}

# ---- HTTP → HTTPS redirect for both domains ----
server {
    listen 80;
    server_name cdp.example.com id.example.com;
    return 301 https://$server_name$request_uri;
}

# ---- Customer 360 API + Frontend (cdp.example.com) ----
server {
    listen 443 ssl http2;
    server_name cdp.example.com;

    ssl_certificate /etc/letsencrypt/live/cdp.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cdp.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # 1) API endpoints
    location /api/v1/ {
        proxy_pass http://c360_api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $server_name;
        proxy_buffering off;
        proxy_request_buffering off;
    }

    # 2) FastAPI docs / health / schema (root-level paths on the API)
    location /health {
        proxy_pass http://c360_api/health;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /docs {
        proxy_pass http://c360_api/docs;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /openapi.json {
        proxy_pass http://c360_api/openapi.json;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # 3) Everything else → frontend SPA
    location / {
        proxy_pass http://c360_frontend/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $server_name;
    }
}

# ---- Keycloak SSO (id.example.com) ----
server {
    listen 443 ssl http2;
    server_name id.example.com;

    ssl_certificate /etc/letsencrypt/live/id.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/id.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    location / {
        proxy_pass http://keycloak/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $server_name;
        proxy_buffering off;
        proxy_request_buffering off;
    }
}
```

Enable and test:

```bash
sudo ln -s /etc/nginx/sites-available/c360.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### Why proxy `/api/v1/` with a trailing slash

```nginx
location /api/v1/ {
    proxy_pass http://c360_api/;
}
```

The trailing slash on both sides strips `/api/v1/` before forwarding, so
`https://cdp.example.com/api/v1/reporting/summary` becomes
`http://c360_api/reporting/summary` internally. The API sees its native
root-level paths and FastAPI's auto-generated `/docs` links work correctly.

---

## 8. Keycloak realm and client configuration

After the stack is up, create the `leocdp` realm and client as described in
[DOCKER-COMPOSE-GUIDE.md §9](DOCKER-COMPOSE-GUIDE.md#9-keycloak-setup-realm-client-first-token),
but use the production URLs below.

### 8.1 Realm

- Name: `leocdp` (must match `KEYCLOAK_REALM`)

### 8.2 Client `leocdp`

| Setting | Value |
|---|---|
| Client ID | `leocdp` |
| Client authentication | **On** (confidential client) |
| Valid redirect URIs | `https://cdp.example.com/*` |
| Web origins | `https://cdp.example.com` |
| Direct access grants | Enable only if you need password-grant tokens (e.g. for scripts) |

After saving, copy the client secret to `.env`:

```dotenv
KEYCLOAK_CLIENT_SECRET=<client-secret-from-keycloak>
```

Then recreate the API container to pick up the new secret:

```bash
docker compose up -d --force-recreate api
```

### 8.3 Tenant claim (for auto-provisioning)

The API reads a custom `tenant_id` claim from the access token to
auto-provision or resolve `sys_user` rows (see
[`customer360-api/core/auth.py`](../customer360-api/core/auth.py)).

Create a **realm** client scope or a dedicated protocol mapper on the
`leocdp` client that publishes `tenant_id` as a hardcoded or user-attribute
claim. Without it, first-time users will authenticate but see no data
because the tenant cannot be resolved.

Example hardcoded realm mapper:

- Name: `tenant_id`
- Mapper type: `Hardcoded claim`
- Token claim name: `tenant_id`
- Claim value: `<the-tenant-uuid>`
- Add to ID token: Off
- Add to access token: **On**

---

## 9. Start the stack

```bash
# 1. Create/update .env
cp .env.example .env
# edit .env with production values

# 2. Build and start
docker compose up -d --build

# 3. Wait for health
docker compose ps
docker compose logs -f
```

Containers should report `healthy` before you expose traffic through Nginx.

---

## 10. Smoke tests

Run these from any machine with access to the public domains.

### 10.1 API health

```bash
curl -s https://cdp.example.com/health
# Expected: {"status":"ok","database":"reachable","sso_login":true}
```

### 10.2 API docs

```bash
curl -s https://cdp.example.com/docs | head
```

### 10.3 Frontend config injection

```bash
curl -s https://cdp.example.com/static/js/config.js | grep apiBase
# Expected: apiBase: "/api/v1"
```

### 10.4 Keycloak well-known endpoint

```bash
curl -s https://id.example.com/realms/leocdp/.well-known/openid-configuration | head
```

### 10.5 Authenticated API call

```bash
TOKEN=$(curl -s -X POST \
  "https://id.example.com/realms/leocdp/protocol/openid-connect/token" \
  -d "client_id=leocdp" \
  -d "client_secret=$KEYCLOAK_CLIENT_SECRET" \
  -d "grant_type=password" \
  -d "username=<test-user>" \
  -d "password=<test-user-password>" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s https://cdp.example.com/api/v1/reporting/summary \
  -H "Authorization: Bearer $TOKEN"
```

### 10.6 Browser test

1. Open `https://cdp.example.com`.
2. Confirm the footer shows the API base as `https://cdp.example.com/api/v1`
   (or `/api/v1`).
3. Open the browser DevTools Network tab and confirm data calls hit
   `https://cdp.example.com/api/v1/...` with HTTP 200 responses.

---

## 11. Security hardening checklist

- [ ] All host-published ports (`POSTGRES_HOST_BIND`, `REDIS_HOST_BIND`,
      `C360_API_HOST`, `KEYCLOAK_HOST_BIND`, `FRONTEND_HOST_BIND`) are
      `127.0.0.1`. Only Nginx listens on `0.0.0.0:443`.
- [ ] `.env` is owned by root with mode `600` and is never committed.
- [ ] `KEYCLOAK_ADMIN_PASSWORD`, `KEYCLOAK_CLIENT_SECRET`, `DB_PASSWORD`, and
      `REDIS_PASSWORD` are strong random values.
- [ ] `KEYCLOAK_COMMAND=start` (production mode) is set.
- [ ] `KEYCLOAK_VERIFY_SSL=true` is set.
- [ ] TLS 1.2/1.3 only; HSTS enabled in Nginx if desired.
- [ ] Certbot auto-renewal is configured.
- [ ] Keycloak admin console is protected by strong credentials and, ideally,
      IP-restricted at the Nginx level.
- [ ] The `tenant_id` protocol mapper is configured so users are provisioned
      into the correct tenant.
- [ ] `GOOGLE_GENAI_API_KEY` is removed or left as the placeholder unless the
      feature is required.

---

## 12. Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| Browser loads UI but API calls fail with 404 | Nginx `location /api/v1/` proxy path is wrong; verify trailing slashes. |
| Browser loads UI but API calls fail with CORS | You are still using a full `FRONTEND_API_HOSTNAME` like `https://cdp.example.com`; leave it empty so the browser uses same-origin `/api/v1`. |
| `/docs` returns 404 | Add explicit `/docs` and `/openapi.json` locations in Nginx. |
| Keycloak redirect goes to `http://localhost:8080` | `KEYCLOAK_HOSTNAME` is still `localhost`; set it to `id.example.com` and recreate the container. |
| API returns `Invalid or expired token` | `KEYCLOAK_CLIENT_SECRET` mismatch, or `SSO_LOGIN_URL` is not reachable from the API container (should be `http://keycloak:8080` internally, already overridden by compose). |
| Authenticated user sees empty data | Access token is missing the `tenant_id` custom claim; add a Keycloak protocol mapper. |
| Keycloak admin console inaccessible | `KEYCLOAK_COMMAND=start-dev` was used; switch to `start` and ensure TLS is terminated by Nginx. |
| Container `frontend` never healthy | Check `docker compose logs frontend`; verify `FRONTEND_API_HOSTNAME=` is accepted and port `8890` is not in use. |

---

## 13. Alternative: subdomains vs. paths

If you later obtain additional subdomains, the cleaner architecture is:

- `https://cdp.example.com` → frontend only
- `https://api.cdp.example.com` → API only
- `https://id.example.com` → Keycloak

To migrate, remove the `/api/v1/` path location from the `cdp.example.com`
server block, create a new `api.cdp.example.com` server block, and set
`FRONTEND_API_HOSTNAME=https://api.cdp.example.com`. No API code changes are
required.
