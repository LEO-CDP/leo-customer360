# deployments/frontend — admin UI (frontend-admin)

Deploys `frontend-admin` (FastAPI/uvicorn, port **8890**) — the Customer 360 admin
UI. It serves HTML/JS and injects runtime config; **the browser**, not this server,
calls the API and Keycloak. So its only dependency is that both are reachable from
the client via the LB (they are).

| Env | Where | Why |
|-----|-------|-----|
| `uat`  | container on the **api box** (`c360-api-uat-api`, server key `api`) | browser-facing tier (api+keycloak) with headroom; the backend box (Dagster) is memory-full |
| `prod` | container on a **dedicated vServer** (server key `frontend`) | keep the public web tier off the API/DB boxes |

No secrets — all config is non-secret and lives in `overlays/<env>.tfvars`.

## Deploy

```bash
./deploy-frontend.sh uat            # build + run on the api box :8890
./deploy-frontend.sh uat destroy    # remove it
```

`deploy-frontend.sh` discovers the target VM from `../server` (by `frontend_server_key`),
ships `frontend-admin/`, builds the image (stripping the BuildKit `--mount`), and runs
it `--network host` with an env file built locally and shipped base64-encoded.

Key env (from the overlay):
- `FRONTEND_API_HOSTNAME` — the **public** API URL the browser uses (uat: `http://103.245.254.29:80`); the page sets `api_base = <host>/api/v1`.
- `FRONTEND_ROOT_PATH=""` — serve at the LB root. The app mounts static at `/static`, but the template's `static_base` is `FRONTEND_ROOT_PATH/static`; a non-empty prefix only works behind an L7 proxy that strips it, so keep it empty on the L4 LB.
- `SSO_LOGIN` — must match customer360-api.
- `FRONTEND_TENANT_ID` — default tenant.

## Expose via the load balancer

`deployments/load_balancer` has a `frontend` backend: LB `:8890 → 10.100.1.5:8890`
(HTTP health check on `/health`), and the per-backend security-group rule opens 8890.
Open the UI at `http://103.245.254.29:8890`.

## Dependencies (all satisfied on uat)
- API public via LB (`:80`) and Keycloak public via LB (`:8080`) — the browser reaches both.
- API CORS `allow_origins=["*"]` — cross-origin calls from `:8890` work.
- Keycloak client redirect URIs include the frontend origin (currently `*`).
- `SSO_LOGIN` consistent between the frontend and the API.
