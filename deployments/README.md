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
| [`load_balancer`](./load_balancer) | L4 NLB fronting api / dagster / keycloak / frontend / ads |
| [`storage`](./storage) | Object storage (vStorage / S3) |

> **Scope:** this view shows the **UAT** overlay only. The prod overlay differs
> (dedicated boxes, managed MemStore, own VPC `10.101.0.0/16`) and will be added
> here once it is provisioned.

## UAT deployment view

![Customer 360 — UAT deployment view](./deployment-view-uat.png)

📐 **Editable sources:** [`deployment-view-uat.excalidraw`](./deployment-view-uat.excalidraw)
(open at [excalidraw.com](https://excalidraw.com) or the Obsidian Excalidraw plugin) ·
[`deployment-view-uat.svg`](./deployment-view-uat.svg) (vector source of the image above).

<details><summary>Same view as a Mermaid diagram (editable text)</summary>

```mermaid
flowchart TB
  client([Client / public internet])
  lb["L4 Network Load Balancer<br/>103.245.254.29<br/>:80→api · :3000→dagster · :8080→keycloak · :8890→frontend · :9009→ads"]
  client --> lb

  subgraph vpc["VPC c360-vpc-uat · subnet 10.100.1.0/24 · HCM03-1C"]
    direction TB
    subgraph apibox["vServer c360-api-uat-api · 10.100.1.5 (s-general-1x2)"]
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
  end

  lb -->|":80 → :8008"| api
  lb -->|":3000"| dagster
  lb -.->|":8080"| kc
  lb -.->|":8890"| fe
  lb -.->|":9009"| ads
  api -->|cache| redis
  api -.->|introspect SSO| kc
  api -->|SQL| pg
  api -.->|GraphQL| dagster
  kc -->|db_keycloak| pg
  ads -->|leo_ads| pg
  dagster -->|SQL| pg
```

</details>

### Components (UAT)

| Component | Runs on | Port | Notes |
|-----------|---------|------|-------|
| L4 NLB | VNG vLB | 80 / 3000 / 8080 / 8890 / 9009 | public `103.245.254.29`; TCP passthrough (no TLS) |
| customer360-api | api box `10.100.1.5` | 8008 | FastAPI, `--network host`; Redis cache; SSO via Keycloak introspection (`SSO_LOGIN=true`) |
| c360-redis | api box `10.100.1.5` | 6580 | fail-open response cache; `maxmemory 256mb allkeys-lru` |
| c360-keycloak | api box `10.100.1.5` | 8080 | Keycloak 26 `start-dev`; health on mgmt `:9000`; realm `customer360` |
| frontend-admin | api box `10.100.1.5` | 8890 | FastAPI admin UI; browser calls the API/Keycloak via the LB |
| ads-server | api box `10.100.1.5` | 9009 | LEO Ad Server (FastAPI); own schema `leo_ads` (no RLS); reuses the local Redis |
| Dagster | backend box `10.100.1.4` | 3000 | backend-system worker |
| PostgreSQL | managed vDB `10.100.1.3` | 5432 | `customer360` (FORCE RLS) + `db_keycloak` + `leo_ads` |

### Public endpoints (via the LB)

| Path | Endpoint |
|------|----------|
| frontend-admin (UI) | `http://103.245.254.29:8890` |
| customer360-api | `http://103.245.254.29:80` |
| ads-server | `http://103.245.254.29:9009` |
| Dagster | `http://103.245.254.29:3000` |
| Keycloak | `http://103.245.254.29:8080` |

### Data flows

- **Client → LB → services** — the NLB forwards `:80→api:8008`, `:3000→dagster:3000`, `:8080→keycloak:8080`, `:8890→frontend:8890`, `:9009→ads:9009`.
- **Browser → frontend-admin** — loads the UI (`:8890`); its JS then calls the API + Keycloak from the browser via the LB.
- **api ⇄ keycloak (SSO)** — the API validates Bearer tokens by OIDC **introspection** against realm `customer360` (token needs a `tenant_id` claim + the client in its audience).
- **ads-server → postgres** — its own `leo_ads` schema (no RLS) in the same managed DB; also uses the co-located Redis (`127.0.0.1:6580`).
- **api → redis** — `127.0.0.1:6580` (co-located, `--network host`).
- **api → postgres** — private `10.100.1.3:5432`, database `customer360` (tenant-scoped via RLS).
- **api → dagster** — GraphQL at `10.100.1.4:3000`.
- **keycloak → postgres** — database `db_keycloak` on the same managed instance.

> ⚠️ UAT is HTTP-only (L4, no TLS) and the Keycloak admin console is publicly
> reachable — fine for testing, not for production. Prod should add a DNS name,
> TLS-terminating L7, and locked-down admin access.
