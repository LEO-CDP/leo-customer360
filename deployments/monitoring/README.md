# deployments/monitoring — Portainer + Netdata dashboards

Deploys two self-contained monitoring UIs onto a server VM as Docker containers. They
cover the two things you actually want on the shared box: **operate the containers** and
**watch the metrics**.

| Tool | Container | URL | Job |
|------|-----------|-----|-----|
| **Portainer** | `c360-portainer` | `https://<box>:9443` | container status/health, **live logs**, exec/console, start/stop/restart, per-container CPU/mem |
| **Netdata** | `c360-netdata` | `http://<box>:19999` | real-time host + **per-container** + Redis metrics, with alarms |

| Env | Where | Why |
|-----|-------|-----|
| `uat`  | both containers on the **api box** (`c360-api-uat-api`, server key `api`) | one shared box already runs api/ads/frontend/keycloak/redis — monitor it from itself |
| `prod` | api box by default | set `mon_server_key` to a dedicated box in `overlays/prod.tfvars` to isolate monitoring once prod has load |

No **required** secrets. DB/Redis are untouched. An **optional** Portainer admin password
lives in `.env` (git-ignored); Netdata has no auth.

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

## Public access via the LB, gated by Keycloak (oauth2-proxy)

The LB is an **L4 NLB** — it forwards TCP and cannot do OIDC, and neither Portainer CE nor
the Netdata agent authenticate against Keycloak natively. So `deploy-monitoring.sh` runs
**oauth2-proxy** on the box as a confidential client (`c360-oauth2-proxy`) in the existing
`customer360` realm, and the LB targets the proxy, not the dashboard:

```
browser → LB :9443  (TCP) → box :4443 oauth2-proxy → [Keycloak login] → https://127.0.0.1:9443  Portainer
browser → LB :19999 (TCP) → box :4199 oauth2-proxy → [Keycloak login] → http://127.0.0.1:19999  Netdata
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

Then browse (each prompts a Keycloak login):
`http://103.245.254.29:9443/` (Portainer) and `http://103.245.254.29:19999/` (Netdata).

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

## Gotchas

- **Port 9000 is taken.** Keycloak's management/health endpoint owns `:9000` on this box,
  so Portainer runs on `9443` (its HTTPS default) — do **not** move it to `9000`. Other
  in-use ports to dodge if you add more: redis `6580`, api `8008`, keycloak `8080`,
  frontend `8890`, ads `9009`.
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
