# deployments/monitoring — Portainer + Netdata + Jaeger

Deploys self-contained observability UIs onto a server VM as Docker containers. They
cover the three things you actually want on the shared box: **operate the containers**,
**watch the metrics**, and **trace API requests**.

| Tool | Container | URL | Job |
|------|-----------|-----|-----|
| **Portainer** | `c360-portainer` | `https://<box>:9443` | container status/health, **live logs**, exec/console, start/stop/restart, per-container CPU/mem |
| **Netdata** | `c360-netdata` | `http://<box>:19999` | real-time host + **per-container** + Redis metrics, with alarms |
| **Jaeger** | `c360-jaeger` | `http://<box>:16686` | **API request traces** (OpenTelemetry/OTLP): per-request waterfall incl. every SQL query, Redis + outbound-HTTP hops, stitched across services |

| Env | Where | Why |
|-----|-------|-----|
| `uat`  | both containers on the **api box** (`c360-api-uat-api`, server key `api`) | one shared box already runs api/ads/frontend/keycloak/redis — monitor it from itself |
| `prod` | api box by default | set `mon_server_key` to a dedicated box in `overlays/prod.tfvars` to isolate monitoring once prod has load |

No **required** secrets. DB/Redis are untouched. An **optional** Portainer admin password
lives in `.env` (git-ignored); Netdata has no auth.

> **First run — set `PORTAINER_ADMIN_PASSWORD` in `.env` BEFORE deploying.** Portainer CE
> locks its first-run setup screen ("Portainer instance timed out for security purposes")
> if no admin is created within a few minutes of the container starting — and going
> through the LB + Keycloak login easily blows that window. Setting the password makes the
> deploy bootstrap the admin non-interactively (`--admin-password-file`), so the lock never
> triggers:
>
> ```bash
> cp .env.example .env
> echo "PORTAINER_ADMIN_PASSWORD=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)" >> .env
> grep '^PORTAINER_ADMIN_PASSWORD=' .env   # copy it to your password manager
> ```
>
> If you already hit the lock, add the password then redeploy; if it persists, wipe the
> volume first: `docker rm -f c360-portainer && docker volume rm portainer_data`. Log in
> at the dashboard as `admin` with that password (after the Keycloak gate).

## Deploy

```bash
./deploy-monitoring.sh uat            # (re)deploy the enabled pieces on the api box
./deploy-monitoring.sh uat destroy    # remove all monitoring containers (data volumes kept)
```

`deploy-monitoring.sh` discovers the target VM from `../server` (by `mon_server_key`),
installs Docker if missing, and runs each enabled container `--restart unless-stopped`.
Re-running is idempotent (`docker rm -f` then `run`). Toggle either tool via
`portainer_enabled` / `netdata_enabled` in the overlay. Portainer binds **loopback-only**
(`127.0.0.1:9443`); Netdata listens on `19999`. Neither is reachable from the internet
until the LB backend (below) is added — and then only through the Keycloak SSO gate.

## Public access via the LB

The LB is an **L4 NLB** — it forwards TCP and cannot do OIDC. Auth is **per dashboard**
(`portainer_sso` / `netdata_sso` in the overlay):

- **Portainer → direct** (`portainer_sso = false`). It has its own login, and its CSRF/origin
  check **rejects mutating requests behind a reverse proxy** ("Forbidden - origin invalid" —
  e.g. creating a tag), so it must NOT sit behind oauth2-proxy. The LB targets Portainer's
  own port; it serves **HTTPS/self-signed**, so the public URL is `https://…:9443`.
- **Netdata → gated** (`netdata_sso = true`). It has no auth of its own, so it stays behind
  **oauth2-proxy** — a confidential `c360-oauth2-proxy` client in the `customer360` realm.

```
browser → LB :9443  (TCP) ───────────────────────────────────→ https://127.0.0.1:9443  Portainer (own login)
browser → LB :19999 (TCP) → box :4199 oauth2-proxy → [Keycloak] → http://127.0.0.1:19999  Netdata
```

### Deploy order

One script does the dashboards **and** the SSO gate (both driven by the overlay; the gate
runs when `oauth2_enabled = true`). It provisions the Keycloak client + secret, generates
the cookie secret (needs the KC admin password — reused from `../sso/.env` if not in
`./.env`), and runs a proxy per enabled dashboard.

```bash
# 1) dashboards + SSO gate (overlays/uat.tfvars)
./deploy-monitoring.sh uat

# 2) open the LB ports (portainer + netdata backends already in overlays/uat.tfvars)
(cd ../load_balancer && ./deploy.sh uat apply)
```

Then browse:
- `https://103.245.254.29:9443/` (Portainer) — accept the self-signed cert, then Portainer's own login.
- `http://103.245.254.29:19999/` (Netdata) — prompts a Keycloak login.

To run **without** the gate, set `oauth2_enabled = false` and redeploy (dashboards stay
tunnel-only). `./deploy-monitoring.sh uat destroy` removes everything (volumes kept).

## Reach the dashboards WITHOUT the LB (admin tunnel)

The dashboard ports are firewalled at the security group (only the proxy ports 4443/4199
are LB backends), so for direct admin access tunnel in like you do for the API:

```bash
ssh -i ~/.ssh/c360-api_ed25519 \
    -L 9443:localhost:9443 -L 19999:localhost:19999 \
    leocdp360@<floating_ip>
# then browse:
#   https://localhost:9443   (Portainer — set the admin user on first visit)
#   http://localhost:19999   (Netdata)
```

## Jaeger — API request tracing (OpenTelemetry → OTLP)

Lightweight, Jaeger-style request profiling for the three FastAPI services
(`customer360-api`, `ads-server`, `frontend-admin`) via **OpenTelemetry zero-code
auto-instrumentation** exporting over **OTLP**.

**How it works.** Each service image runs `opentelemetry-bootstrap -a install` (installs
the FastAPI / SQLAlchemy / Redis / psycopg2 / httpx instrumentors) and launches uvicorn
under `opentelemetry-instrument` — **no application code changes**. You get one span per
HTTP request, plus child spans for every SQL query, Redis call, and outbound HTTP hop,
with W3C `traceparent` propagation so a request crossing services is a single trace. Spans
export over OTLP/HTTP to the `c360-jaeger` container deployed here (v1 all-in-one, **badger**
on-disk storage, `--memory`-capped, UI `:16686`, OTLP `:4318`/`:4317`). The exporter env is
generated by `../lib/otel.sh` (`otel_env_lines`) and injected into each service's container
env-file by its deploy script. Because it's all OTLP, the backend is swappable (VNG managed
tracing / Grafana Tempo / an OTel Collector) by changing one endpoint — no re-instrumentation.

**Policy.**

| Env | Tracing default | Sampling | Jaeger |
|-----|-----------------|----------|--------|
| **UAT** | **OFF** (`OTEL_SDK_DISABLED=true`) — the api box is a shared 1 vCPU / 2 GB host, so zero overhead until profiling | 100% when on | on-demand (`jaeger_enabled=false`) |
| **PROD** | **ON** | 10% head sampling | always-on (`jaeger_enabled=true`) |

Override per deploy with `OTEL_ENABLED` / `OTEL_ENDPOINT` / `OTEL_SAMPLER_ARG` (read by
`../lib/otel.sh`). Toggle the backend with `jaeger_enabled` in the overlay.

**Profiling UAT on demand.** Services run `--network host`, so they reach Jaeger at
`127.0.0.1:4318`.

```bash
# 1) start Jaeger on the api box
sed -i 's/^jaeger_enabled *=.*/jaeger_enabled = true/' overlays/uat.tfvars
./deploy-monitoring.sh uat            # starts c360-jaeger (badger, mem-capped)

# 2) turn tracing on for the target service and redeploy
(cd ../server && OTEL_ENABLED=true ./deploy-api.sh uat)     # or ../ads-server, ../frontend
#    fast path (no redeploy): flip the env-file on the box and restart the container
#    ssh <box> 'sudo sed -i "s/^OTEL_SDK_DISABLED=.*/OTEL_SDK_DISABLED=false/" /opt/c360/api.env && sudo docker restart customer360-api'

# 3) view the trace UI. Primary: LB + Keycloak SSO (like Netdata) at http://<lb-ip>:16686
#    — jaeger_sso=true + oauth2_enabled are already set; make sure the LB backend is applied:
#    (cd ../load_balancer && ./deploy.sh uat apply)
#    Fallback (no LB): tunnel to the loopback UI —
ssh -i ~/.ssh/c360-api_ed25519 -L 16686:localhost:16686 leocdp360@<api-box-fip>  # then http://localhost:16686

# 4) revert: OTEL_SDK_DISABLED=true (redeploy or the sed+restart above), and optionally
#    jaeger_enabled=false + ./deploy-monitoring.sh uat to stop Jaeger and reclaim RAM.
```

**PROD.** Nothing to toggle — `jaeger_enabled=true` in `overlays/prod.tfvars` and services
deploy with tracing on at 10% sampling. Per-service boxes reach the monitoring box's Jaeger
over the private VPC (the deploy scripts resolve the `mon_server_key` box's private IP). OTLP
ports are published on `0.0.0.0`; the **UI is gated behind oauth2-proxy / Keycloak and exposed
via the LB on `:16686`** (like Netdata — `jaeger_sso=true` + the `jaeger` backend in
`../load_balancer/overlays/`; fill `REPLACE_WITH_PROD_API_IP` in the prod LB overlay).

**Local docker-compose dev** (not wired by default). Add a `jaeger` service to
`docker-compose.yml` (`jaegertracing/all-in-one:1.62`, `COLLECTOR_OTLP_ENABLED=true`, ports
`16686` + `4318`) and set on the api/cir services: `OTEL_SDK_DISABLED=false`,
`OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`, `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`,
`OTEL_SERVICE_NAME=<svc>`.

## Gotchas

- **Port 9000 is taken.** Keycloak's management/health endpoint owns `:9000` on this box,
  so Portainer runs on `9443` (its HTTPS default) — do **not** move it to `9000`. Other
  in-use ports to dodge if you add more: redis `6580`, api `8008`, keycloak `8080`,
  frontend `8890`, ads `9009`, jaeger UI `16686` + OTLP `4317`/`4318`.
- **Jaeger memory on the tiny box.** `c360-jaeger` uses **badger** on-disk storage (not
  in-memory) and a `--memory` cap (`jaeger_mem`, `300m` on uat) so it can't starve the
  shared 1 vCPU / 2 GB box. On uat it's off by default — start it only while profiling
  (see the Jaeger section) and stop it (`jaeger_enabled=false` + redeploy) when done.
- **Enabling Jaeger SSO on an existing env needs a redirect sync.** The Jaeger UI is gated like
  Netdata (oauth2-proxy → Keycloak → LB `:16686`). But `deploy-monitoring.sh` **skips** the
  Keycloak client bootstrap when `OAUTH2_PROXY_CLIENT_SECRET` is already in `.env`, so the new
  Jaeger callback (`http://<oauth2_public_host>:16686/oauth2/callback`) is NOT auto-registered on
  the existing `c360-oauth2-proxy` client — login then fails with *"Invalid redirect_uri"*. Fix:
  add that URI under the client's **Valid redirect URIs** in Keycloak, **or** comment out
  `OAUTH2_PROXY_CLIENT_SECRET` in `.env` and re-run `./deploy-monitoring.sh <env>` (the bootstrap
  upserts all current redirect URIs and rewrites the same secret).
- **Netdata is unauthenticated at the agent.** The `:19999` dashboard has no login of its
  own — public access is safe ONLY because the LB fronts it with oauth2-proxy/Keycloak.
  Note it still listens on `0.0.0.0:19999`, so other hosts *inside the VPC subnet* can
  reach it directly (bypassing SSO); tighten with a `netdata.conf [web] bind to = 127.0.0.1`
  override if that matters. Portainer already binds loopback-only.
- **oauth2-proxy needs Keycloak reachable at the issuer URL.** `oauth2_issuer_url` is the
  public LB Keycloak URL; the box hairpins to the LB to fetch OIDC discovery. The `iss` in
  tokens must equal this exactly (it's `KC_HOSTNAME/realms/customer360`).
- **UAT rides HTTP end-to-end** (no TLS on the L4 LB), so oauth2-proxy sets
  `--cookie-secure=false`. For a TLS'd prod, flip that and use `https://` callback URLs.
- **Portainer behind a reverse proxy = "Forbidden - origin invalid".** Portainer's CSRF
  check compares the request `Origin` to its own host; an oauth2-proxy/L7 hop in front
  breaks that on POST/PUT (e.g. creating a tag). That's why Portainer is exposed **directly**
  (`portainer_sso = false`) rather than gated. If you must gate it, you'd need Portainer EE
  (native OIDC) or a proxy that rewrites Origin to match — not worth it here.
- **Portainer setup-timeout lock.** If you don't create the admin user within a few
  minutes of first start, Portainer locks the init screen for security — either set
  `PORTAINER_ADMIN_PASSWORD` in `.env` (non-interactive bootstrap) or
  `docker restart c360-portainer` and try again in the UI.
- **Changing Netdata's port** needs a `netdata.conf` `[web] default port` override
  mounted into `/etc/netdata`; the script keeps it at the `19999` default.

## Cost

Both are **free** (open source) and run on a VM you already pay for — **$0** in
licenses/subscriptions. They're the lightweight choice on the `s-general-1x2`
(1 vCPU / 2 GB) box: Portainer ~50 MB, Netdata ~100–200 MB, neither with a heavy TSDB. A
full **Prometheus + Grafana + cAdvisor + exporters** stack is also free software, but its
~0.5–1 GB RAM + steady CPU would likely force a VM upsize on a 2 GB box — that upsize is
the only real cost. Reserve it for when monitoring gets its own box.

## Dependencies

- `../server` — provides the target VM (floating IP looked up by `mon_server_key`).
- Docker on the box (installed by the script if missing).
- Read-only `docker.sock` (Netdata) / `docker.sock` (Portainer) for container discovery
  and control — no app config, no DB, no Redis credentials.
- `../sso` (only for public access) — Keycloak `customer360` realm; `deploy-monitoring.sh`
  registers the `c360-oauth2-proxy` client and reuses `KEYCLOAK_ADMIN_PASSWORD` from
  `../sso/.env`. The realm must already exist (`../sso/bootstrap-realm.py`).
- `../load_balancer` (only for public access) — the `portainer` + `netdata` backends that
  expose the proxy ports; `cd ../load_balancer && ./deploy.sh <env> apply`.
