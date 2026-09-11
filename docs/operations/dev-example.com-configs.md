# NGINX config for local development

`c360.example.com` is the main local-dev hostname. Add it to `/etc/hosts`:

```
127.0.0.1 c360.example.com
127.0.0.1 s3dev.example.com
```

The applications must be configured for the public prefixes before starting
the services. For the `/c360/ai/ask` flow, the browser calls the frontend-admin
proxy; `DOCS_SEARCH_URL` is the private local upstream and must not be set to
the public `/c360/ai/ask` URL:

- `customer360-api`: `root_path=/c360api`
- Keycloak: `KC_HTTP_RELATIVE_PATH=/auth`
- `ads-server`: `LEO_AD_ROOT_PATH=/ads`
- `frontend-admin`: `FRONTEND_ROOT_PATH=/c360`, `FRONTEND_API_HOSTNAME=https://c360.example.com/c360api`, and `DOCS_SEARCH_URL=http://127.0.0.1:8001`
- `docs-vector-search`: start locally on `127.0.0.1:8001`; `dev-c360.sh` builds the image, refreshes the index, and starts it
- `data-tracking-api`: no root path; nginx strips `/data`
- Dagster: start the UI with `--path-prefix /dagster`
- MinIO console: `MINIO_BROWSER_REDIRECT_URL=https://s3dev.example.com/minio/`

For a local shell setup, the relevant entries in the repository root `.env`
are:

```dotenv
FRONTEND_ROOT_PATH=/c360
FRONTEND_API_HOSTNAME=https://c360.example.com/c360api
DOCS_SEARCH_HOST_PORT=8001
DOCS_SEARCH_URL=http://127.0.0.1:8001
DOCS_SEARCH_TIMEOUT=120
DOCS_LLM_MAX_OUTPUT_TOKENS=256
DOCS_RERANK_ENABLED=true
```

Start the local AI service together with the rest of the development stack:

```bash
./dev-c360.sh
curl -fsS http://127.0.0.1:8001/health
```

The browser-facing AI endpoint is then:

```text
https://c360.example.com/c360/ai/ask
```

Nginx routes that request to `frontend-admin`; `frontend-admin` forwards the
request to `http://127.0.0.1:8001/ask`.

With this configuration, the public endpoints are:

| Service | Public URL | Local upstream |
| --- | --- | --- |
| frontend-admin (UI) | `https://c360.example.com/c360/` | `127.0.0.1:8890` |
| frontend-admin AI proxy | `https://c360.example.com/c360/ai/ask` | `127.0.0.1:8890` -> `127.0.0.1:8001/ask` |
| customer360-api | `https://c360.example.com/c360api/api/v1` | `127.0.0.1:8008` |
| Keycloak | `https://c360.example.com/auth` | `127.0.0.1:8080` |
| ads-server and docs | `https://c360.example.com/ads` and `/ads/docs` | `127.0.0.1:9009` |
| data-tracking-api | POST `https://c360.example.com/data/api/v1/tracking/logs`; health `https://c360.example.com/data/health` | `127.0.0.1:8010` |
| Dagster UI | `https://c360.example.com/dagster` | `127.0.0.1:3000` |
| MinIO S3 API | `https://s3dev.example.com` | `127.0.0.1:9000` |
| MinIO console | `https://s3dev.example.com/minio` | `127.0.0.1:9001` |

Configure S3-compatible clients that run outside the MinIO host network with
the public endpoint:

```bash
S3_ENDPOINT_URL=https://s3dev.example.com
S3_BROWSER_REDIRECT_URL=https://s3dev.example.com/minio/
S3_FORCE_PATH_STYLE=true
```

MinIO itself uses `http://minio:9000` internally for console authentication;
`S3_ENDPOINT_URL=https://s3dev.example.com` remains the public endpoint for
host-side clients and the tracking/analytics services.

The cross-origin web SDK iframe is served by `data-tracking-api`, not the admin
frontend. Because the embedding page is `https://example.com` and the iframe
origin is `https://c360.example.com`, do not use `X-Frame-Options: SAMEORIGIN`;
use the iframe route's `frame-ancestors` policy below instead.

```nginx
############# C360 local dev #######
####################################

# c360 web admin
upstream c360_frontend {
  server 127.0.0.1:8890;
}

# c360 core API
upstream c360_core_api {
  server 127.0.0.1:8008;
}

# Keycloak
upstream c360_keycloak {
  server 127.0.0.1:8080;
}

# ads-server
upstream c360_ads {
  server 127.0.0.1:9009;
}

# c360 tracking API
upstream c360_tracking_api {
  server 127.0.0.1:8010;
}

# local Dagster UI
upstream c360_dagster_backend {
  server 127.0.0.1:3000;
}

# local S3 API by MinIO
upstream c360_minio_api {
  server 127.0.0.1:9000;
}

# local MinIO web console
upstream c360_minio_console {
  server 127.0.0.1:9001;
}

server {
  server_name c360.example.com;

  # frontend-admin uses /c360 as its public root path. Keep the AI routes
  # prefixed because FastAPI registers /c360/ai/* when FRONTEND_ROOT_PATH=/c360.
  location = /c360 {
    return 308 /c360/;
  }

  location ^~ /c360/ai/ {
    proxy_pass http://c360_frontend;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 120s;
  }

  # Strip /c360 before forwarding the UI and static assets. The frontend app
  # serves / and /static internally, while its generated URLs remain /c360/*.
  location ^~ /c360/ {
    proxy_pass http://c360_frontend/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 600s;
  }

  # customer360-api: preserve /c360api for root_path and generated URLs.
  location = /c360api {
    proxy_pass http://c360_core_api;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 600s;
  }

  location ^~ /c360api/ {
    proxy_pass http://c360_core_api;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 600s;
  }

  # Keycloak: preserve /auth because KC_HTTP_RELATIVE_PATH is /auth.
  location = /auth {
    proxy_pass http://c360_keycloak;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 600s;
  }

  location ^~ /auth/ {
    proxy_pass http://c360_keycloak;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 600s;
  }

  # ads-server: preserve /ads for LEO_AD_ROOT_PATH=/ads.
  location = /ads {
    proxy_pass http://c360_ads;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 600s;
  }

  location ^~ /ads/ {
    proxy_pass http://c360_ads;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 600s;
  }

  # data-tracking-api: strip /data so /data/health reaches /health.
  location = /data {
    return 308 /data/;
  }

  location ^~ /data/ {
    proxy_pass http://c360_tracking_api/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 600s;
  }

  # c360 web SDK iframe endpoint is served by data-tracking-api at
  # /cdp-sdk/html/cdp-event-proxy.html. Public URL can be prefixed with /data
  # (for example /data/cdp-sdk/html/cdp-event-proxy.html) because this block
  # strips /data before forwarding to the upstream service.

  # Dagster UI: use `dagster dev ... --path-prefix /dagster`.
  location = /dagster {
    return 308 /dagster/;
  }

  location ^~ /dagster/ {
    proxy_pass http://c360_dagster_backend/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 600s;
  }

  # All other paths go to the admin UI.
  location / {
    proxy_pass http://c360_frontend/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    access_log off;
  }

  listen 443 ssl http2;
  ssl_certificate /home/thomas/0-uspa/localhost-ssl/example.com+5.pem;
  ssl_certificate_key /home/thomas/0-uspa/localhost-ssl/example.com+5-key.pem;
}

# MinIO uses a dedicated local-dev hostname. The S3 API is served at the host
# root, while the web console remains under /minio/ on the same hostname.
server {
  server_name s3dev.example.com;
  
  # Allow any size file to be uploaded to MinIO. Prevents HTTP 413 errors on large uploads.
  client_max_body_size 0;
  
  # Disable buffering for smoother streaming and large file uploads
  proxy_buffering off;
  proxy_request_buffering off;

  location = /minio {
    return 308 /minio/;
  }

  location ^~ /minio/ {
    proxy_pass http://c360_minio_console/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 600s;
    
    # REQUIRED WEBSOCKET HEADERS ADDED HERE
    # Upgrades the HTTP connection to a WebSocket connection for MinIO Console components
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
  }

  location / {
    proxy_pass http://c360_minio_api/;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 600s;
  }

  listen 443 ssl http2;
  ssl_certificate /home/thomas/0-uspa/localhost-ssl/example.com+5.pem;
  ssl_certificate_key /home/thomas/0-uspa/localhost-ssl/example.com+5-key.pem;
}
```
