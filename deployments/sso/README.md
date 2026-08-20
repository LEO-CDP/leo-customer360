# deployments/sso — Keycloak (SSO / OIDC) for customer360-api

Deploys the `keycloak` service from `docker-compose.yml` onto the GreenNode VMs.
Keycloak backs the API's `SSO_LOGIN=true` auth mode (OIDC token validation); with
`SSO_LOGIN=false` the API uses a local dev JWT and Keycloak is not required.

| Env | Where Keycloak runs | Mode |
|-----|---------------------|------|
| `uat`  | **Docker container on the same VM as customer360-api** (server key `api`) | `start-dev` (HTTP, lenient hostname) |
| `prod` | **Docker container on a dedicated vServer** (server key `sso`) | `start` (HTTP behind the LB; TLS at the LB) |

Both use the managed PostgreSQL database **`db_keycloak`** (already created by
`postgres/init/02-create-keycloak-db.sql`). No Terraform lives here — the VMs are
provisioned by `deployments/server`; this deployment is a deploy script only.

## Setup

```bash
cp .env.example .env      # set KEYCLOAK_ADMIN_PASSWORD
```

The DB password is reused from `../postgres` (no need to duplicate it).

## Deploy

```bash
./deploy-sso.sh uat            # Keycloak container on the api box (:8080)
./deploy-sso.sh uat destroy    # remove it
./deploy-sso.sh prod           # Keycloak on the dedicated 'sso' vServer
```

`deploy-sso.sh` discovers the target VM's public IP from `../server` (by the
`sso_server_key` overlay value), SSHes in, and runs:

```
docker run -d --name c360-keycloak --restart unless-stopped --network host \
  -e KC_DB=postgres -e KC_DB_URL=jdbc:postgresql://<db>:5432/db_keycloak \
  -e KC_DB_USERNAME=<user> -e KC_DB_PASSWORD=*** \
  -e KC_HOSTNAME=<host> -e KC_HTTP_PORT=8080 -e KC_HEALTH_ENABLED=true \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=*** \
  keycloak/keycloak:26.7 <start-dev|start>
```

Readiness is polled at `http://127.0.0.1:9000/health/ready` (Keycloak 26 serves
`/health/*` on the management port **9000**, not the app port 8080).

### uat memory note
The uat box (`s-general-1x2`, 2 GB) also runs customer360-api + Redis, so the
overlay caps Keycloak's heap (`java_heap = "-Xms256m -Xmx512m"`). If the box gets
memory-tight, bump its flavor in `../server/overlays/uat.tfvars` or move Keycloak
to its own box (as prod does).

## Wire the API to Keycloak

customer360-api uses OIDC **authorization-code** login and validates tokens by
**introspection** (a confidential client), and it **requires a `tenant_id` claim** on
the token. `bootstrap-realm.py` provisions all of that idempotently:

```bash
# needs KEYCLOAK_ADMIN_PASSWORD + KC_TEST_USER_PASSWORD in .env
KC_URL=http://103.245.254.29:8080 REALM=customer360 CLIENT_ID=customer360-api \
  TENANT_ID=11111111-1111-1111-1111-111111111111 TEST_USER=c360admin REDIRECT_URIS='*' \
  python3 bootstrap-realm.py
```

It creates: the realm; a confidential client (standard flow + direct grants);
protocol mappers for `tenant_id`, `user_id`, **and an audience mapper** (Keycloak 24+
introspection returns `active:false` unless the introspecting client is in the token
`aud`); enables **unmanaged attributes** (Keycloak 26 drops undeclared attributes like
`tenant_id` otherwise); and a test user with `tenant_id`. The client secret is written
to `.env` as `KEYCLOAK_CLIENT_SECRET`.

Then enable it for the API (already set in `overlays/uat.tfvars`):
`api_sso_enabled=true`, `api_sso_login_url` (the **public LB URL** — used by both the
browser redirect and the backend introspection), `api_keycloak_realm`,
`api_keycloak_client_id`. Re-deploy:

```bash
../server/deploy-api.sh uat    # prints ">> SSO: ENABLED ..."; injects SSO_LOGIN=true + KEYCLOAK_*
```

Verify headlessly (direct-grant token -> protected endpoint):

```bash
# token for c360admin, then:
curl -H "Authorization: Bearer <token>" http://103.245.254.29:80/api/v1/users/me   # -> 200, auto-provisioned
```

> **Note:** auto-provisioning a first-time Keycloak user writes RLS-protected
> `sys_user`/`sys_userinfo`, so `core.auth._get_or_create_user_on_login` sets
> `app.tenant_id` on that session (fixed in this repo) — otherwise the RLS `::uuid`
> cast fails on the managed (non-superuser) DB.

## Public exposure (via the load balancer)

uat Keycloak is exposed through the L4 NLB: `deployments/load_balancer` has a
`keycloak` backend (`103.245.254.29:8080 -> 10.100.1.5:8080`, TCP health check —
Keycloak's `/health` is on mgmt port 9000, so a path check on 8080 wouldn't work),
and the LB's per-backend security-group rule opens 8080 on the box.

Because the public entry point is the LB, `keycloak_hostname` is set to
`http://103.245.254.29:8080` so the OIDC issuer / admin redirect URLs match what
clients actually hit (verified: `GET /realms/master/.well-known/openid-configuration`
returns `issuer = http://103.245.254.29:8080/realms/master`). Put a DNS name +
TLS-terminating L7 in front for anything beyond testing.
