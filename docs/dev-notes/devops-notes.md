# Customer 360 — DevOps Quick Reference

Last updated: 2026-07-30

## 1. Default ports and host bindings

The defaults below come from [.env.example](../.env.example). Copy that file to `.env` and adjust the values before the first run.

| Service | Default host bind / port | Internal port | Notes |
|---|---|---|---|
| PostgreSQL | `127.0.0.1:5432` | 5432 | Main datastore; also backs Keycloak `db_keycloak` |
| Redis | `127.0.0.1:6580` | 6580 | API response cache and auth token cache |
| Keycloak | `127.0.0.1:8080` | 8080 | Admin console: http://localhost:8080/admin |
| MinIO S3 API | `127.0.0.1:9000` | 9000 | Dev-only S3-compatible object storage |
| MinIO Console | `127.0.0.1:9001` | 9001 | Web UI for the dev MinIO bucket |
| Dagster UI | `127.0.0.1:3000` | 3000 | Only when running backend-system directly on the host |
| C360 API | `127.0.0.1:8008` | 8008 | FastAPI app; all endpoints except `/health` require a bearer token |
| C360 Frontend | `0.0.0.0:8890` | 8890 | Static admin UI served by frontend-admin |

> The published ports are configurable through `.env` using `*_HOST_PORT` and `*_HOST_BIND` variables. The defaults in [.env.example](../.env.example) are loopback-only for safety.

## 2. Environment setup

Create the local environment file before first use:

```bash
cp .env.example .env
```

At a minimum, set real values for:

- `DB_PASSWORD`
- `REDIS_PASSWORD`
- `KEYCLOAK_ADMIN_PASSWORD`
- `KEYCLOAK_CLIENT_SECRET` (create the client in Keycloak first; see [DOCKER-COMPOSE-GUIDE.md](DOCKER-COMPOSE-GUIDE.md))

Recommended additional values for a local setup:

- `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` if you want the dev MinIO stack
- `GOOGLE_GENAI_API_KEY` if you want live LLM-backed persona generation; otherwise the placeholder keeps CIR behavior deterministic/offline

`.env` is gitignored; `.env.example` is the committed template.

## 3. Useful commands

Production / full stack (Postgres, Redis, Keycloak, CIR, API):

```bash
./manage-c360.sh start
./manage-c360.sh status
./manage-c360.sh logs api
```

Local dev / infra-only stack (Postgres, Redis, Keycloak, MinIO) while running API/CIR directly on the host:

```bash
./dev-c360.sh
```

Raw `docker compose` equivalents:

```bash
docker compose up -d --build                 # production stack
docker compose --profile dev up -d --build   # production stack + demo seed job
docker compose -f dev-docker-compose.yml up -d --build  # infra-only dev stack
```

### Important: Building custom images on new PC or server

Some Docker images must be built from Dockerfiles before running (e.g., `customer360-postgres:local`). The `--build` flag above rebuilds all images. For first-time setup or if `docker compose up` fails with missing image errors, run explicit builds:

```bash
# Build images for production stack
docker compose build

# Build images for dev-only infra stack
docker compose -f dev-docker-compose.yml build
```

These commands can be run standalone (before `up`) or combined with `up -d --build` as shown above.

## 4. Compose and networking notes

- [docker-compose.yml](../docker-compose.yml) and [dev-docker-compose.yml](../dev-docker-compose.yml) share the same project name, container names, network, and volumes. Do not run both files at the same time.
- `DB_HOST`, `REDIS_HOST`, and `SSO_LOGIN_URL` are overridden inside the `api` and `cir` containers to use the Docker service names `postgres`, `redis`, and `keycloak`. The `localhost` values in [.env.example](../.env.example) are only for host-run services.
- Keycloak 26 exposes `/health/*` on the management port `9000` internally; the published port remains `8080`.
- The public-facing API and frontend bindings default to loopback. Change them to `0.0.0.0` only when the host is otherwise trusted or when a reverse proxy is in front of the stack.

## 5. Health checks

Check all containers:

```bash
docker compose ps
```

Useful health checks:

| Service | Check |
|---|---|
| API | `curl -s http://localhost:8008/health` |
| Keycloak | `curl -s http://localhost:8080/health/ready` |
| Postgres | `docker exec customer360-postgres pg_isready -U postgres -d customer360` |
| Redis | `docker exec customer360-redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping` |

## 6. Local PC development setup

### Quick start

On your local machine (macOS, Linux, Windows with WSL2):

```bash
# 1. Clone the repo
git clone <repo-url>
cd leo-customer360

# 2. Copy and configure the environment
cp .env.example .env
# Edit .env and set at least:
#   - DB_PASSWORD (a strong password)
#   - REDIS_PASSWORD (a strong password)
#   - KEYCLOAK_ADMIN_PASSWORD (a strong password)
#   - KEYCLOAK_CLIENT_SECRET (generate a UUID or strong string)

# 3. Build and start the full stack
# (custom images like customer360-postgres:local are built from Dockerfiles)
docker compose build    # build custom images once
docker compose up -d    # start containers
# OR combined: docker compose up -d --build

# 4. Wait for services to be healthy (watch logs)
docker compose logs -f

# 5. Verify the stack
curl -s http://localhost:8008/health | jq .
# Output: {"status": "ok"}
```

### Accessing services locally

Add the following to your `/etc/hosts` (or `C:\Windows\System32\drivers\etc\hosts` on Windows):

```
127.0.0.1 customer360.local
127.0.0.1 keycloak.local
127.0.0.1 minio.local
```

Then access:

- **C360 API**: http://customer360.local:8008 or http://localhost:8008
- **C360 Admin Frontend**: http://customer360.local:8890 or http://localhost:8890
- **Keycloak Admin**: http://keycloak.local:8080 or http://localhost:8080
- **MinIO Console**: http://minio.local:9001 or http://localhost:9001

### Running API/CIR locally (not in Docker)

For faster iteration, run the API and CIR worker directly on the host while keeping Postgres, Redis, and Keycloak containerized:

```bash
# Terminal 1: First time only — build dev infra images
docker compose -f dev-docker-compose.yml build

# Terminal 1: Start infra-only
./dev-c360.sh

# Terminal 2: Start the API
cd customer360-api
./start.sh

# Terminal 3: Start CIR worker
cd backend-system/identity_resolution
./run-demo.sh  # or ./worker.py for production mode
```

The API will use the `.env` file and connect to `localhost:5432` (Postgres), `localhost:6580` (Redis), and `localhost:8080` (Keycloak).

### Development gotchas

- **Keycloak setup**: Before you can authenticate against the API, you must create a Keycloak client. See [DOCKER-COMPOSE-GUIDE.md](DOCKER-COMPOSE-GUIDE.md) for step-by-step instructions.
- **Bearer tokens**: All API endpoints except `/health`, `/api/v1/metadata`, and `/api/v1/auth/*` require a valid bearer token in both modes. Use the Keycloak web UI or admin API to generate test tokens.
- **SSO_LOGIN=true**: Set this in `.env` to require real Keycloak tokens. Set to `false` for local testing without Keycloak -- call `POST /api/v1/auth/login` (DEFAULT_ROOT_USERNAME/PASSWORD) to get a dev JWT instead; `X-Tenant-Id`/`X-User-Id` headers alone no longer bypass auth.
- **FRONTEND_API_HOSTNAME**: Must be reachable from your browser. If running behind a reverse proxy, update this to the proxy's public URL.

## 7. Server deployment setup

### Pre-deployment checklist

Before deploying to a server, ensure:

1. **DNS and hostname** are configured and resolve to the server's IP.
2. **TLS certificates** (Let's Encrypt or internal CA) are available.
3. **Firewall rules** allow traffic to ports 80, 443 (and optionally 22 for SSH).
4. **Reverse proxy** (Nginx, HAProxy) is set up and configured.
5. **Storage** for PostgreSQL data is sized for your expected data volume.
6. **Backups** for PostgreSQL and Redis volumes are configured.

### Environment configuration for server

```bash
# On the server:
cp .env.example .env

# Edit .env for production:
#   - DB_PASSWORD: strong random password
#   - REDIS_PASSWORD: strong random password
#   - KEYCLOAK_ADMIN_PASSWORD: strong random password
#   - KEYCLOAK_CLIENT_SECRET: strong random password
#   - POSTGRES_HOST_BIND: 127.0.0.1 (keep internal)
#   - REDIS_HOST_BIND: 127.0.0.1 (keep internal)
#   - API_HOST_BIND: 127.0.0.1 (reverse proxy in front)
#   - KEYCLOAK_HOST_BIND: 127.0.0.1 (reverse proxy in front)
#   - FRONTEND_HOST_BIND: 127.0.0.1 (reverse proxy in front)
#   - FRONTEND_API_HOSTNAME: https://api.customer360.example.com (public URL)
#   - KEYCLOAK_HOSTNAME: customer360.example.com (public hostname)
#   - SSO_LOGIN_URL: https://customer360.example.com/auth (or use SSO endpoint)
#   - GOOGLE_GENAI_API_KEY: (optional) if using LLM features
```

### Reverse proxy configuration (Nginx)

Create `/etc/nginx/sites-available/customer360.conf`:

```nginx
# Upstream definitions
upstream c360_api {
    server 127.0.0.1:8008;
}

upstream c360_frontend {
    server 127.0.0.1:8890;
}

upstream keycloak {
    server 127.0.0.1:8080;
}

# HTTP → HTTPS redirect
server {
    listen 80;
    server_name customer360.example.com api.customer360.example.com auth.customer360.example.com;
    return 301 https://$server_name$request_uri;
}

# HTTPS - API endpoint
server {
    listen 443 ssl http2;
    server_name api.customer360.example.com;

    ssl_certificate /etc/letsencrypt/live/customer360.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/customer360.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    location / {
        proxy_pass http://c360_api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $server_name;
        proxy_buffering off;
        proxy_request_buffering off;
    }
}

# HTTPS - Admin frontend
server {
    listen 443 ssl http2;
    server_name customer360.example.com;

    ssl_certificate /etc/letsencrypt/live/customer360.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/customer360.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    location / {
        proxy_pass http://c360_frontend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $server_name;
    }
}

# HTTPS - Keycloak SSO (optional, if exposing directly)
server {
    listen 443 ssl http2;
    server_name auth.customer360.example.com;

    ssl_certificate /etc/letsencrypt/live/customer360.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/customer360.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    location / {
        proxy_pass http://keycloak;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $server_name;
    }
}
```

Enable and test:

```bash
sudo ln -s /etc/nginx/sites-available/customer360.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### Startup and monitoring

Start the full stack:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f
```

Monitor resource usage:

```bash
docker stats
docker compose logs --tail=50 api cir keycloak
```

### Backup and disaster recovery

#### PostgreSQL backups

```bash
# Full backup (run daily, store offsite)
docker exec customer360-postgres pg_dump -U postgres -F c customer360 > /backups/customer360-$(date +%Y%m%d).dump

# Point-in-time recovery
docker exec customer360-postgres pg_restore -U postgres -d customer360_new /backups/customer360-YYYYMMDD.dump
```

#### Redis snapshot

```bash
# Redis snapshots are stored in the volume; verify it exists
docker inspect customer360-redis | grep Mounts
```

#### Volume backup

```bash
# Backup Postgres volume
docker run --rm -v customer360-pgdata:/data -v /backups:/backup ubuntu tar czf /backup/pgdata-$(date +%Y%m%d).tar.gz -C / data

# Backup Redis volume
docker run --rm -v customer360-redisdata:/data -v /backups:/backup ubuntu tar czf /backup/redisdata-$(date +%Y%m%d).tar.gz -C / data
```

### Scaling considerations

- **Single-server production**: Suitable for < 100k profiles and low QPS. Monitor CPU and memory.
- **Multi-server production**: Run PostgreSQL separately (managed service like AWS RDS) and scale the API/CIR workers independently.
- **Database tuning**: Tune `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, and `DB_POOL_RECYCLE_SECONDS` based on load.
- **Caching**: Increase `CACHE_TTL_SECONDS` to reduce database load; tune `REDIS_DB` if sharing Redis with other apps.

## 8. Hosts and DNS configuration

### Local development hosts

Add to `/etc/hosts`:

```
127.0.0.1 customer360.local
127.0.0.1 api.customer360.local
127.0.0.1 keycloak.local
127.0.0.1 minio.local
127.0.0.1 postgres.local
127.0.0.1 redis.local
```

### Server production hosts

Create DNS records (or add to `/etc/hosts` for testing before DNS is live):

```
customer360.example.com       A  <server-ip>     # Main frontend/admin
api.customer360.example.com    A  <server-ip>     # API endpoint
auth.customer360.example.com   A  <server-ip>     # Keycloak (optional)
```

Update `.env` on the server:

```bash
KEYCLOAK_HOSTNAME=customer360.example.com
FRONTEND_API_HOSTNAME=https://api.customer360.example.com
SSO_LOGIN_URL=https://auth.customer360.example.com
KEYCLOAK_CLIENT_SECRET=<strong-secret>
```

### Environment variable mapping

| Variable | Local PC | Server | Notes |
|---|---|---|---|
| `DB_HOST` | `localhost` | `postgres` (docker) or external hostname | Only matters if running services on the host |
| `REDIS_HOST` | `localhost` | `redis` (docker) or external hostname | Only matters if running services on the host |
| `SSO_LOGIN_URL` | `http://localhost:8080` | `https://auth.customer360.example.com` | Used by API to verify tokens |
| `KEYCLOAK_HOSTNAME` | `localhost` | `customer360.example.com` | Public hostname Keycloak advertises |
| `FRONTEND_API_HOSTNAME` | `http://localhost:8008` | `https://api.customer360.example.com` | Used by the browser to call the API |
| `API_HOST_BIND` | `0.0.0.0` or `127.0.0.1` | `127.0.0.1` | Only change to `0.0.0.0` if firewall is restrictive |
| `POSTGRES_HOST_BIND` | `127.0.0.1` | `127.0.0.1` | Always keep loopback-only unless running a multi-host cluster |
| `REDIS_HOST_BIND` | `127.0.0.1` | `127.0.0.1` | Always keep loopback-only unless running a multi-host cluster |
| `KEYCLOAK_HOST_BIND` | `127.0.0.1` | `127.0.0.1` | Always keep loopback-only; use reverse proxy for public access |


