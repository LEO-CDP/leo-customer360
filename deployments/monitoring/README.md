# deployments/monitoring — Portainer + Netdata + Jaeger + pgAdmin

Deploys self-contained observability UIs onto a server VM as Docker containers. They
cover the things you actually want on the shared box: **operate the containers**,
**watch the metrics**, **trace API requests**, and **inspect the database**.

| Tool | Container | URL | Job |
|------|-----------|-----|-----|
| **Portainer** | `c360-portainer` | `https://<box>:9443` | container status/health, **live logs**, exec/console, start/stop/restart, per-container CPU/mem |
| **Netdata** | `c360-netdata` | `http://<box>:19999` | real-time host + **per-container** + Redis metrics, with alarms |
| **Jaeger** | `c360-jaeger` | `https://<domain>/jaeger` | **API request traces** (OpenTelemetry/OTLP): per-request waterfall incl. every SQL query, Redis + outbound-HTTP hops, stitched across services |
| **pgAdmin** | `c360-pgadmin` | `http://<lb-ip>:5050` (own login) | **Postgres admin/monitoring**: browse schemas/tables, run SQL, view locks/activity/index usage. Its own login (email+password), exposed directly on the LB |

| Env | Where | Why |
|-----|-------|-----|
| `uat`  | both containers on the **api box** (`c360-api-uat-api`, server key `api`) | one shared box already runs api/ads/frontend/keycloak/redis — monitor it from itself |
| `prod` | api box by default | set `mon_server_key` to a dedicated box in `overlays/prod.tfvars` to isolate monitoring once prod has load |

No **required** secrets for Portainer/Netdata/Jaeger. DB/Redis are untouched. An **optional**
Portainer admin password lives in `.env` (git-ignored); Netdata has no auth. **pgAdmin** needs
a login password (`PGADMIN_DEFAULT_PASSWORD` in `.env`) — the deploy auto-generates and saves
one on first run if you don't set it; pgAdmin is exposed **directly on the LB with its own login**
(`pgadmin_sso = false`, `pgadmin_bind = 0.0.0.0`) — see the cleartext caveat in its section below.

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
    -L 9443:localhost:9443 -L 19999:localhost:19999 -L 5050:localhost:5050 \
    leocdp360@<floating_ip>
# then browse:
#   https://localhost:9443   (Portainer — set the admin user on first visit)
#   http://localhost:19999   (Netdata)
#   http://localhost:5050    (pgAdmin — log in with admin@leocdp.com / PGADMIN_DEFAULT_PASSWORD)
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
#    fast path (no full redeploy): flip the env-file on the box then RE-CREATE the container.
#    NOTE: `docker restart` does NOT re-read --env-file (it's applied only at `docker run`), so a
#    restart alone leaves tracing OFF — you must rm + run (reusing the same image):
#    ssh <box> 'sudo sed -i "s/^OTEL_SDK_DISABLED=.*/OTEL_SDK_DISABLED=false/" /opt/c360/api.env; \
#      img=$(sudo docker inspect customer360-api --format "{{.Config.Image}}"); \
#      sudo docker rm -f customer360-api; \
#      sudo docker run -d --name customer360-api --restart unless-stopped --network host --env-file /opt/c360/api.env "$img"'
#    (then send a request to the service — Jaeger lists a service only after it receives >=1 span)

# 3) view the trace UI at https://<domain>/jaeger (Caddy :443 TLS -> oauth2 -> Keycloak; login c360admin)
#    jaeger_sso + oauth2_enabled are set; make sure Caddy has the /jaeger route:
#    (cd ../proxy && ./deploy-caddy.sh uat)
#    Fallback (no LB): tunnel to the loopback UI —
ssh -i ~/.ssh/c360-api_ed25519 -L 16686:localhost:16686 leocdp360@<api-box-fip>  # then http://localhost:16686

# 4) revert: OTEL_SDK_DISABLED=true (redeploy or the sed+restart above), and optionally
#    jaeger_enabled=false + ./deploy-monitoring.sh uat to stop Jaeger and reclaim RAM.
```

**PROD.** Nothing to toggle — `jaeger_enabled=true` in `overlays/prod.tfvars` and services
deploy with tracing on at 10% sampling. Per-service boxes reach the monitoring box's Jaeger
over the private VPC (the deploy scripts resolve the `mon_server_key` box's private IP). OTLP
ports are published on `0.0.0.0`; the **UI is gated behind oauth2-proxy / Keycloak and exposed
via **Caddy at `/jaeger` on :443 (TLS)** (like Netdata — `jaeger_sso=true`; the upstream in
`../load_balancer/overlays/`; fill `REPLACE_WITH_PROD_API_IP` in the prod LB overlay).

**Local docker-compose dev** (not wired by default). Add a `jaeger` service to
`docker-compose.yml` (`jaegertracing/all-in-one:1.62`, `COLLECTOR_OTLP_ENABLED=true`, ports
`16686` + `4318`) and set on the api/cir services: `OTEL_SDK_DISABLED=false`,
`OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`, `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`,
`OTEL_SERVICE_NAME=<svc>`.

## pgAdmin — Postgres admin / monitoring

Web UI for the platform's PostgreSQL: browse schemas/tables, run ad-hoc SQL, and watch DB
health (server activity, locks, index usage, table bloat) — the DB-side complement to
Netdata's host/container metrics and Jaeger's per-query traces.

**How it works.** `c360-pgadmin` runs `dpage/pgadmin4` on the monitoring box, bridge net,
listening on container `:80` published to `pgadmin_bind:pgadmin_port` (`127.0.0.1:5050`). Its
config DB, saved server connections, and sessions persist in the `pgadmin_data` volume. Memory
is `--memory`-capped (`pgadmin_mem`) — it's the heaviest of the four tools on the shared uat box.

**Access = direct on the LB, pgAdmin's own login (uat default).** Like Portainer, pgAdmin has its
own login, so it's exposed directly (`pgadmin_sso = false`, `pgadmin_bind = 0.0.0.0`): the LB
forwards straight to it and pgAdmin authenticates.

```
browser → LB :5050 (TCP) → pgAdmin 0.0.0.0:5050 (own login)
```

The LB backend is `member_port = 5050`, `listen_port = 5050`, health `/misc/ping` in
`../load_balancer/overlays/<env>.tfvars`.

> ⚠️ **Cleartext caveat.** Unlike Portainer (self-signed HTTPS), pgAdmin serves **plain HTTP**,
> and the uat L4 LB has no TLS — so the DB-admin login page is public and credentials cross the
> wire in cleartext. This is a deliberate uat tradeoff for a single login. **To harden:** gate it
> behind Keycloak (`pgadmin_sso = true` + `pgadmin_bind = 127.0.0.1`, then a `c360-oauth2-pgadmin`
> proxy fronts it on `pgadmin_proxy_port` 4050 and the LB backend uses `member_port = 4050`,
> health `/ping` — reachability control, still HTTP transit), or front it with **Caddy TLS** like
> Jaeger (real encryption). Tunnel-only (leave the LB backend off + `ssh -L 5050:localhost:5050`)
> is the most conservative.

**First login.** pgAdmin: email `pgadmin_email` (default `admin@leocdp.com`), password
`PGADMIN_DEFAULT_PASSWORD` from `.env` (auto-generated + saved on first deploy). Then _Add New
Server_ → point host at the Postgres box's private IP / the `postgres` service, with the app DB
credentials (pgAdmin only stores them if you tick "Save").

**Deploy.**

```bash
# 1) deploy pgAdmin (pgadmin_enabled already set in overlays/uat.tfvars)
./deploy-monitoring.sh uat

# 2) open the LB port (the 'pgadmin' backend is in overlays/uat.tfvars)
(cd ../load_balancer && ./deploy.sh uat apply)

# then browse http://<lb-ip>:5050  → pgAdmin login (admin@leocdp.com)
# admin (no LB): ssh -i ~/.ssh/c360-api_ed25519 -L 5050:localhost:5050 leocdp360@<api-box-fip>
# turn it off to reclaim RAM on the tiny box: set pgadmin_enabled = false + redeploy
#
# NOTE: switching an EXISTING 'pgadmin' LB backend between gated (:4050) and direct (:5050)
# changes the pool member_port, which the VNG provider tries as an in-place update and the API
# REJECTS ("Stickiness cannot be specified for non-HTTP pools"). Force a pool replace instead:
#   terraform apply -replace='vngcloud_vlb_pool.this["pgadmin"]' -var-file=overlays/uat.tfvars
```

## Portainer agents — one Portainer, every box

Portainer runs on the api box (`mon_server_key`) and by default sees only that box's Docker
socket. The platform spans more than one vServer (the **backend** box, server key `1x2` /
`10.100.1.4`, runs `backend-system`/Dagster) — so rather than stand up a **second** Portainer,
run a lightweight **`portainer/agent`** on the other box and register it in the existing Portainer
as another *Environment*. One login, one UI, every box in the list.

```
browser → LB :9443 → Portainer (api box)  ──local socket──→  api-box containers
                                          ──private VPC :9001 (agent, mTLS)──→ backend-box containers
```

Driven by `portainer_agent_server_keys` in the overlay (comma-separated `../server` keys):

```hcl
portainer_agent_server_keys = "1x2"           # backend box; add more keys comma-separated
portainer_agent_image       = "portainer/agent:lts"   # match your portainer-ce:lts
portainer_agent_port        = 9001
```

`deploy-monitoring.sh` then, for each key: runs `c360-portainer-agent` on that box (reached via
its floating IP) and **auto-registers** it in Portainer via the API (needs
`PORTAINER_ADMIN_PASSWORD` in `.env`; otherwise it prints the one-click UI step — *Environments →
Add → Agent → `<private-ip>:9001`*). Idempotent — re-runs skip an already-registered environment.

> **Prerequisite (infra, one-time):** the VNG Default secgroup opens nothing inbound, so Portainer
> can't reach the agent until you open `tcp/9001` on the boxes' secgroup **from the Portainer box's
> private IP**. That rule lives in `../server/overlays/<env>.tfvars` as `extra_ingress`
> (`{ port = 9001, cidr = "10.100.1.5/32" }` for uat) — apply it out-of-band (CD never runs infra
> Terraform): `cd ../server && ./deploy.sh <env> apply`. Traffic stays on the private VPC; `:9001`
> is never public.

## Gotchas

- **Port 9000 is taken.** Keycloak's management/health endpoint owns `:9000` on this box,
  so Portainer runs on `9443` (its HTTPS default) — do **not** move it to `9000`. Other
  in-use ports to dodge if you add more: redis `6580`, api `8008`, keycloak `8080`,
  frontend `8890`, ads `9009`, jaeger UI `16686` + OTLP `4317`/`4318`, pgAdmin `5050`
  (+ its oauth2 gate `4050` when `pgadmin_sso`).
- **pgAdmin is direct + cleartext (`pgadmin_sso = false`, `pgadmin_bind = 0.0.0.0`).** It serves
  plain HTTP and uat's L4 LB has no TLS, so its DB-admin login page is public and credentials go
  over the wire in cleartext — a deliberate uat tradeoff for a single login. Harden by gating it
  (`pgadmin_sso = true`) or fronting it with Caddy TLS. It's the heaviest tool here — set
  `pgadmin_enabled = false` to run it on-demand if the shared box gets tight.
- **Switching pgAdmin's LB backend between gated and direct needs a pool `-replace`.** Gated uses
  `member_port = 4050` (oauth2-proxy), direct uses `member_port = 5050` (pgAdmin). Changing the
  member_port on an existing `pgadmin` pool makes the VNG provider attempt an **in-place update**,
  which the API rejects: *"Stickiness cannot be specified for non-HTTP pools"* — leaving the LB
  half-applied (secgroup flipped, pool not). `./deploy.sh` can't express a replace, so run it
  directly: `terraform apply -replace='vngcloud_vlb_pool.this["pgadmin"]' -var-file=overlays/<env>.tfvars`
  (the listener cascades via its `replace_triggered_by`).
- **Gating pgAdmin later needs a redirect sync** (same trap as Jaeger). If you set
  `pgadmin_sso = true`, `deploy-monitoring.sh` **skips** the Keycloak client bootstrap when
  `OAUTH2_PROXY_CLIENT_SECRET` is already in `.env`, so pgAdmin's callback
  (`http://<oauth2_public_host>:5050/oauth2/callback`) isn't auto-registered on the
  `c360-oauth2-proxy` client → *"Invalid redirect_uri"*. Fix: add that URI in Keycloak, **or**
  comment out `OAUTH2_PROXY_CLIENT_SECRET` in `.env` and re-run (bootstrap upserts all URIs).
- **pgAdmin volume permissions.** The config lives in the `pgadmin_data` named volume mounted
  at `/var/lib/pgadmin` (owned by the image's `pgadmin` user). Don't switch it to a host bind
  mount without `chown`-ing to UID 5050, or pgAdmin fails to write its config DB on boot.
- **Jaeger memory on the tiny box.** `c360-jaeger` uses **badger** on-disk storage (not
  in-memory) and a `--memory` cap (`jaeger_mem`, `300m` on uat) so it can't starve the
  shared 1 vCPU / 2 GB box. On uat it's off by default — start it only while profiling
  (see the Jaeger section) and stop it (`jaeger_enabled=false` + redeploy) when done.
- **Enabling Jaeger SSO on an existing env needs a redirect sync.** The Jaeger UI is gated like
  Netdata (oauth2-proxy → Keycloak → LB `:16686`). But `deploy-monitoring.sh` **skips** the
  Keycloak client bootstrap when `OAUTH2_PROXY_CLIENT_SECRET` is already in `.env`, so the new
  Jaeger callback (`https://<oauth2_public_host>/jaeger/oauth2/callback`) is NOT auto-registered on
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

All four are **free** (open source) and run on a VM you already pay for — **$0** in
licenses/subscriptions. They're the lightweight choice on the `s-general-1x2`
(1 vCPU / 2 GB) box: Portainer ~50 MB, Netdata ~100–200 MB, Jaeger ~250–350 MB (badger),
pgAdmin ~150–250 MB — none with a heavy TSDB. **pgAdmin is the heaviest**, so on the shared
uat box it's `pgadmin_mem`-capped and easy to make on-demand (`pgadmin_enabled = false`); the
same is true of Jaeger. Running all four at once approaches the 2 GB budget — turn off what
you aren't using. A full **Prometheus + Grafana + cAdvisor + exporters** stack is also free
software, but its ~0.5–1 GB RAM + steady CPU would likely force a VM upsize on a 2 GB box —
that upsize is the only real cost. Reserve it for when monitoring gets its own box.

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
