#!/usr/bin/env python3
"""Idempotently provision a Keycloak confidential client for oauth2-proxy.

oauth2-proxy fronts the monitoring dashboards (Portainer + Netdata) and delegates
login to Keycloak via OIDC authorization-code. This registers (or updates) a single
confidential client `c360-oauth2-proxy` in the EXISTING `customer360` realm, with the
two dashboard callback URLs as redirect URIs, then writes the client secret back into
./.env as OAUTH2_PROXY_CLIENT_SECRET (never printed).

Unlike bootstrap-realm.py this needs NO tenant/user mappers — oauth2-proxy only needs a
valid login; any realm user may pass. Restrict later with a group/role + oauth2-proxy
`--allowed-group` if you want to limit who reaches the dashboards.

Config comes from env vars (set by deploy-monitoring.sh from .env + the overlay):
  KC_URL, KC_ADMIN_USER(=admin), KEYCLOAK_ADMIN_PASSWORD,
  REALM(=customer360), CLIENT_ID(=c360-oauth2-proxy), REDIRECT_URIS (comma-separated)
"""
import json, os, sys, urllib.request, urllib.parse, urllib.error

KC_URL = os.environ["KC_URL"].rstrip("/")
ADMIN_USER = os.environ.get("KC_ADMIN_USER", "admin")
ADMIN_PW = os.environ["KEYCLOAK_ADMIN_PASSWORD"]
REALM = os.environ.get("REALM", "customer360")
CLIENT_ID = os.environ.get("CLIENT_ID", "c360-oauth2-proxy")
REDIRECT_URIS = [u for u in os.environ.get("REDIRECT_URIS", "").split(",") if u]
ENV_FILE = os.environ.get("ENV_FILE", os.path.join(os.path.dirname(__file__), ".env"))


def req(method, path, token=None, body=None, form=None):
    url = path if path.startswith("http") else f"{KC_URL}{path}"
    headers = {}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip() else {}), resp.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, (json.loads(raw) if raw.strip().startswith(("{", "[")) else {"_raw": raw}), ""


def admin_token():
    st, body, _ = req("POST", "/realms/master/protocol/openid-connect/token", form={
        "client_id": "admin-cli", "username": ADMIN_USER, "password": ADMIN_PW, "grant_type": "password"})
    if st != 200:
        sys.exit(f"ERROR: could not get admin token (HTTP {st}): {body}")
    return body["access_token"]


def main():
    if not REDIRECT_URIS:
        sys.exit("ERROR: REDIRECT_URIS is required (the oauth2-proxy callback URLs).")
    tok = admin_token()

    # realm must already exist (created by bootstrap-realm.py for customer360-api).
    st, _, _ = req("GET", f"/admin/realms/{REALM}", token=tok)
    if st == 404:
        sys.exit(f"ERROR: realm '{REALM}' does not exist — run deployments/sso/bootstrap-realm.py first.")

    # confidential client: standard flow only (browser code flow); no direct grants.
    st, clients, _ = req("GET", f"/admin/realms/{REALM}/clients?clientId={urllib.parse.quote(CLIENT_ID)}", token=tok)
    desired = {
        "clientId": CLIENT_ID, "enabled": True, "protocol": "openid-connect",
        "publicClient": False, "standardFlowEnabled": True, "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False, "redirectUris": REDIRECT_URIS, "webOrigins": ["+"],
    }
    if clients:
        cuid = clients[0]["id"]
        req("PUT", f"/admin/realms/{REALM}/clients/{cuid}", token=tok, body={**clients[0], **desired})
        print(f"client '{CLIENT_ID}': exists (updated redirect URIs)")
    else:
        st, _, loc = req("POST", f"/admin/realms/{REALM}/clients", token=tok, body=desired)
        cuid = loc.rstrip("/").split("/")[-1]
        print(f"client '{CLIENT_ID}': created" if st in (201, 204) else f"client create HTTP {st}: {clients}")

    # client secret
    st, sec, _ = req("GET", f"/admin/realms/{REALM}/clients/{cuid}/client-secret", token=tok)
    secret = sec.get("value", "")
    if not secret:
        st, sec, _ = req("POST", f"/admin/realms/{REALM}/clients/{cuid}/client-secret", token=tok)
        secret = sec.get("value", "")

    if secret:
        lines, seen = [], False
        if os.path.exists(ENV_FILE):
            with open(ENV_FILE, encoding="utf-8") as f:
                for ln in f:
                    if ln.startswith("OAUTH2_PROXY_CLIENT_SECRET="):
                        lines.append(f"OAUTH2_PROXY_CLIENT_SECRET={secret}\n"); seen = True
                    else:
                        lines.append(ln)
        if not seen:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(f"OAUTH2_PROXY_CLIENT_SECRET={secret}\n")
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)
        print(f"client secret: written to {os.path.basename(ENV_FILE)} (OAUTH2_PROXY_CLIENT_SECRET)")
    else:
        sys.exit("ERROR: could not read client secret")

    print(f"\nDONE. realm={REALM} client={CLIENT_ID} redirect_uris={REDIRECT_URIS}")


if __name__ == "__main__":
    main()
