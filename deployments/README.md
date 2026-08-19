# Customer 360 — Deployments

Infrastructure-as-code + deploy scripts for the Customer 360 platform on
**GreenNode / VNG Cloud** (zone **HCM03-1C**). Each subfolder is a self-contained
deployment with per-env `overlays/<env>.tfvars`, Terraform workspaces, and a
`deploy.sh`-style wrapper.

| Folder | What it provisions |
|--------|--------------------|
| [`postgres`](./postgres) | Managed PostgreSQL vDB (`customer360` + `db_keycloak`), `run-sql.sh` schema/seed bootstrap |
| [`server`](./server) | vServers (VMs): api box + backend box (uat); adds dedicated `sso` + `frontend` + `ads` boxes (prod) |
| [`cache`](./cache) | Redis — uat: container on the api box; prod: managed MemStore |
| [`sso`](./sso) | Keycloak (SSO/OIDC) — uat: container on the api box; prod: dedicated vServer |
| [`frontend`](./frontend) | frontend-admin (admin UI) — uat: container on the api box; prod: dedicated vServer |
| [`ads-server`](./ads-server) | LEO Ad Server (schema `leo_ads`) — uat: container on the api box; prod: dedicated vServer |
| [`monitoring`](./monitoring) | Portainer (direct HTTPS) + Netdata (behind oauth2-proxy / Keycloak SSO) dashboards — on the api box |
| [`load_balancer`](./load_balancer) | L4 NLB fronting api / dagster / keycloak / frontend / ads / monitoring |
| [`proxy`](./proxy) | **Caddy** reverse proxy — TLS termination (auto Let's Encrypt) + single-host path routing (`beta.leocdp.com`). **Staged**: built + validated, not cut over — see its [runbook](./proxy/README.md#cutover-runbook-put-the-platform-behind-betaleocdpcom) |
| [`storage`](./storage) | Object storage (vStorage / S3) |

> **Scope:** this view shows the **UAT** overlay only. The prod overlay differs
> (dedicated boxes, managed MemStore, own VPC `10.101.0.0/16`) and will be added
> here once it is provisioned.

## One-shot deploy — `deploy-all.sh`

[`deploy-all.sh`](./deploy-all.sh) is a single orchestrator that runs **every module's
deploy script in the correct dependency order** for one environment. Each module keeps
its own script (they are the source of truth); this is a thin, re-runnable wrapper on top.

```bash
./deploy-all.sh uat                 # apply everything, in order (default action)
./deploy-all.sh uat --list          # print the ordered steps and exit (no changes)
./deploy-all.sh uat --dry-run        # show the exact commands that would run
./deploy-all.sh uat --with seed     # also load the CIR demo data
./deploy-all.sh uat plan            # terraform plan for the IaC steps only
./deploy-all.sh uat destroy         # tear everything down (REVERSE order)
```

![Customer 360 — deploy pipeline](./deploy-process.png)

📐 **Editable sources:** [`deploy-process.excalidraw`](./deploy-process.excalidraw)
(open at [excalidraw.com](https://excalidraw.com) or the Obsidian Excalidraw plugin) ·
[`deploy-process.svg`](./deploy-process.svg) (vector source of the image above).

### Ordered steps

Phases run top → bottom; every step is idempotent/converging, so re-running is a safe
no-op when there is no drift.

| # | Phase | Step | Runs | Depends on |
|---|-------|------|------|------------|
| 1 | Infra (Terraform) | `storage` | `storage/deploy.sh` | — (independent) |
| 2 | Infra (Terraform) | `postgres` | `postgres/deploy.sh` | — |
| 3 | Infra (Terraform) | `server` | `server/deploy.sh` | — |
| 4 | DB bootstrap | `db-schema` | `postgres/run-sql.sh` | `server` (bastion) + `postgres` |
| 5 | Data-plane | `cache` | `cache/deploy.sh` | `server` |
| 6 | Data-plane | `sso` | `sso/deploy-sso.sh` | `server` + `db-schema` (`db_keycloak`) |
| 7 | Data-plane | `backend` | `server/deploy-backend.sh` | `server` + `db-schema` |
| 8 | Front door | `load-balancer` | `load_balancer/deploy.sh` | `server` |
| 9 | Front door | `proxy` (Caddy) | `proxy/deploy-caddy.sh` | `load-balancer` (`:80`/`:443` → box) + **DNS** |
| 10 | SSO + apps | `sso-realm` | `sso/bootstrap-realm.py` | `sso` + public HTTPS (writes `KEYCLOAK_CLIENT_SECRET` → `sso/.env`) |
| 11 | SSO + apps | `api` | `server/deploy-api.sh` | `db-schema`, `cache`, `backend`, `sso-realm` (SSO optional) |
| 12 | SSO + apps | `frontend` | `frontend/deploy-frontend.sh` | `server` |
| 13 | SSO + apps | `ads` | `ads-server/deploy-ads.sh` | `server` + `db-schema` + `cache` |
| 14 | SSO + apps | `monitoring` | `monitoring/deploy-monitoring.sh` | `server`; SSO gate needs `sso-realm` + `load-balancer` |
| 15 | Demo data | `seed` *(optional)* | `server/seed_data.sh` | `api`/`db-schema`; opt-in via `--with seed` |

### Flags

| Flag | Effect |
|------|--------|
| `--list` | Print the ordered steps (with phases) and exit. |
| `--from <step>` | Start at `<step>`, skip everything before it (**resume** after a fix). |
| `--only <a,b,c>` | Run **only** these steps, keeping order. |
| `--skip <a,b,c>` | Run everything **except** these steps. |
| `--with <a,b,c>` | Also run the **optional** steps (e.g. `seed`). |
| `--keep-going` | Don't stop on the first failure; report at the end. |
| `--dry-run` | Print the commands that **would** run; execute nothing. |
| `-y`, `--yes` | Don't prompt for confirmation before executing. |

### First bring-up & the HTTPS prerequisite

Steps 9–14 that need the **public HTTPS entry point** (Caddy's cert, the API's SSO mode,
the monitoring oauth2 gate) require that DNS for `caddy_domain` (`beta.leocdp.com`) points
at the load balancer and Caddy has issued its Let's Encrypt certificate. On a first
bring-up before DNS is live:

```bash
./deploy-all.sh uat --skip proxy,sso-realm,monitoring   # infra + apps (api starts in local-JWT mode)
# → point DNS: beta.leocdp.com (A) → the LB public IP, wait for it to resolve
./deploy-all.sh uat --from proxy                        # Caddy → realm → re-deploy api (SSO on) → monitoring
```

`deploy-api.sh` falls back to `SSO_LOGIN=false` (dev local-JWT) until the realm exists, so
running `api` early is safe — re-run it after `sso-realm` to switch SSO on. Secrets/creds
live in each module's own `.env` / `terraform.tfvars` (see each module's README).

## UAT deployment view

![Customer 360 — UAT deployment view](./deployment-view-uat.png)

📐 **Editable sources:** [`deployment-view-uat.excalidraw`](./deployment-view-uat.excalidraw)
(open at [excalidraw.com](https://excalidraw.com) or the Obsidian Excalidraw plugin) ·
[`deployment-view-uat.svg`](./deployment-view-uat.svg) (vector source of the image above).

<details><summary>Same view as a Mermaid diagram (editable text)</summary>

```mermaid
flowchart TB
  client([Client / public internet])
  lb["L4 Network Load Balancer<br/>beta.leocdp.com → 103.245.254.29<br/>:443/:80 → Caddy · :3000 → dagster<br/>:9443 → portainer (direct TLS) · :19999 → netdata (SSO)"]
  client --> lb

  subgraph vpc["VPC c360-vpc-uat · subnet 10.100.1.0/24 · HCM03-1C"]
    direction TB
    subgraph apibox["vServer c360-api-uat-api · 10.100.1.5 (s-general-1x2)"]
      caddy["Caddy reverse proxy<br/>:443/:80 · TLS termination + path routing"]
      api["customer360-api (FastAPI)<br/>:8008"]
      redis["c360-redis (Redis 8)<br/>:6580"]
      kc["c360-keycloak (Keycloak 26)<br/>:8080 · health :9000"]
      fe["frontend-admin (admin UI)<br/>:8890"]
      ads["ads-server (LEO Ad Server)<br/>:9009 · leo_ads"]
    end
    subgraph bebox["vServer c360-api-uat-backend · 10.100.1.4"]
      dagster["backend-system<br/>Dagster :3000"]
    end
    pg[("Managed PostgreSQL vDB<br/>10.100.1.3:5432<br/>customer360 (RLS) · db_keycloak · leo_ads")]
    subgraph mon["monitoring · on the api box"]
      oauth2["oauth2-proxy (SSO gate)<br/>:4199 → Netdata · Keycloak"]
      portainer["Portainer<br/>:9443 · own login"]
      netdata["Netdata<br/>:19999"]
    end
  end

  lb -->|":443/:80"| caddy
  lb -->|":3000"| dagster
  caddy -->|"/"| fe
  caddy -->|"/c360api"| api
  caddy -.->|"/auth"| kc
  caddy -.->|"/ads"| ads
  api -->|cache| redis
  api -.->|introspect SSO| kc
  api -->|SQL| pg
  api -.->|GraphQL| dagster
  kc -->|db_keycloak| pg
  ads -->|leo_ads| pg
  dagster -->|SQL| pg
  lb -->|":9443 direct TLS"| portainer
  lb -.->|":19999 SSO"| oauth2
  oauth2 -->|gates| netdata
```

</details>

### Components (UAT)

| Component | Runs on | Port | Notes |
|-----------|---------|------|-------|
| L4 NLB | VNG vLB | 80 / 3000 / 8080 / 8890 / 9009 / 9443 / 19999 | public `103.245.254.29`; TCP passthrough (no TLS) |
| customer360-api | api box `10.100.1.5` | 8008 | FastAPI, `--network host`; Redis cache; SSO via Keycloak introspection (`SSO_LOGIN=true`) |
| c360-redis | api box `10.100.1.5` | 6580 | fail-open response cache; `maxmemory 256mb allkeys-lru` |
| c360-keycloak | api box `10.100.1.5` | 8080 | Keycloak 26 `start-dev`; health on mgmt `:9000`; realm `customer360` |
| frontend-admin | api box `10.100.1.5` | 8890 | FastAPI admin UI; browser calls the API/Keycloak via the LB |
| ads-server | api box `10.100.1.5` | 9009 | LEO Ad Server (FastAPI); own schema `leo_ads` (no RLS); reuses the local Redis |
| oauth2-proxy | api box `10.100.1.5` | 4199 | Keycloak SSO gate in front of Netdata (the L4 LB can't do OIDC) |
| Portainer | api box `10.100.1.5` | 9443 | container ops UI (logs/exec/restart); direct HTTPS on the LB — its own login |
| Netdata | api box `10.100.1.5` | 19999 | real-time host + per-container metrics; no native auth → oauth2-proxy SSO |
| Dagster | backend box `10.100.1.4` | 3000 | backend-system worker |
| PostgreSQL | managed vDB `10.100.1.3` | 5432 | `customer360` (FORCE RLS) + `db_keycloak` + `leo_ads` |

### Public endpoints — `beta.leocdp.com`

The domain (`beta.leocdp.com`, A record → the LB public IP `103.245.254.29`) is fronted by
**Caddy** (deployments/proxy), which terminates TLS and path-routes the browser-facing apps.
The ops dashboards stay on their raw LB ports (they don't sub-path cleanly).

| Service | URL | Served by |
|---------|-----|-----------|
| frontend-admin (UI) | `https://beta.leocdp.com/` | Caddy `/` → frontend :8890 |
| customer360-api | `https://beta.leocdp.com/c360api` (base `…/c360api/api/v1`) | Caddy `/c360api/*` → api :8008 (prefix stripped) |
| Keycloak | `https://beta.leocdp.com/auth` | Caddy `/auth/*` → keycloak :8080 |
| ads-server | `https://beta.leocdp.com/ads` | Caddy `/ads/*` → ads :9009 |
| Portainer (own login) | `https://beta.leocdp.com:9443` | LB direct → Portainer :9443 (self-signed TLS) |
| Netdata (SSO) | `http://beta.leocdp.com:19999` | LB → oauth2-proxy :4199 → Netdata (Keycloak login) |
| Dagster | `http://beta.leocdp.com:3000` | LB direct → dagster :3000 |

> The cutover overlay edits are in [`proxy/cutover-beta.leocdp.com.patch`](./proxy/cutover-beta.leocdp.com.patch);
> the ordered deploy is the [proxy runbook](./proxy/README.md#cutover-runbook-put-the-platform-behind-betaleocdpcom).
> Before the cutover the same services were reachable raw at `http://103.245.254.29:<80|3000|8080|8890|9009>`
> and `https://…:9443` / `http://…:19999`.

### Data flows

- **Client → LB → Caddy → apps** — the NLB passes `:443`/`:80` through to **Caddy** on the api box, which terminates TLS and path-routes `beta.leocdp.com`: `/`→frontend :8890, `/c360api`→api :8008, `/auth`→keycloak :8080, `/ads`→ads :9009. Ops tools stay on raw LB ports (`:3000`→dagster, `:9443`→Portainer, `:19999`→oauth2-proxy→Netdata).
- **Browser → frontend-admin** — loads the UI (`:8890`); its JS then calls the API + Keycloak from the browser via the LB.
- **api ⇄ keycloak (SSO)** — the API validates Bearer tokens by OIDC **introspection** against realm `customer360` (token needs a `tenant_id` claim + the client in its audience).
- **ads-server → postgres** — its own `leo_ads` schema (no RLS) in the same managed DB; also uses the co-located Redis (`127.0.0.1:6580`).
- **monitoring** — **Portainer** is exposed directly (`LB :9443 → Portainer :9443`, L4 TLS passthrough) and uses its own login. **Netdata** has no native auth, so `oauth2-proxy` gates it via Keycloak: `LB :19999 → oauth2-proxy :4199 → [Keycloak login] → Netdata :19999`. Both read the Docker socket for container discovery; no DB/Redis.
- **api → redis** — `127.0.0.1:6580` (co-located, `--network host`).
- **api → postgres** — private `10.100.1.3:5432`, database `customer360` (tenant-scoped via RLS).
- **api → dagster** — GraphQL at `10.100.1.4:3000`.
- **keycloak → postgres** — database `db_keycloak` on the same managed instance.

> ⚠️ The browser-facing apps are now HTTPS via Caddy (`beta.leocdp.com`, auto Let's Encrypt),
> but the ops dashboards still ride raw HTTP/self-signed ports and the Keycloak admin console
> is publicly reachable — fine for testing, not production. For prod, move the ops tools
> behind the domain (subdomains) too and lock down admin access.
