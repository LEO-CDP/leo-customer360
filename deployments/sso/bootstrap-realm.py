#!/usr/bin/env python3
"""Idempotently provision a Keycloak realm + confidential client for customer360-api.

Creates (or updates): a realm, a confidential client (standard flow + direct-access
grants so tokens can be minted headlessly for testing), a `tenant_id` protocol
mapper that publishes the user's tenant_id attribute into the access token AND the
introspection response (customer360-api validates via introspection and REQUIRES a
tenant_id claim), and a test user with that attribute + a password.

On success it writes KEYCLOAK_CLIENT_SECRET back into ./.env (never printed).

Config comes from env vars (deploy-sso.sh-style .env + overlay values):
  KC_URL, KC_ADMIN_USER(=admin), KEYCLOAK_ADMIN_PASSWORD,
  REALM, CLIENT_ID, TENANT_ID, TEST_USER, KC_TEST_USER_PASSWORD, REDIRECT_URIS
"""
import json, os, sys, urllib.request, urllib.parse, urllib.error

KC_URL = os.environ["KC_URL"].rstrip("/")
ADMIN_USER = os.environ.get("KC_ADMIN_USER", "admin")
ADMIN_PW = os.environ["KEYCLOAK_ADMIN_PASSWORD"]
REALM = os.environ.get("REALM", "customer360")
CLIENT_ID = os.environ.get("CLIENT_ID", "customer360-api")
TENANT_ID = os.environ.get("TENANT_ID", "11111111-1111-1111-1111-111111111111")
TEST_USER = os.environ.get("TEST_USER", "c360admin")
TEST_PW = os.environ.get("KC_TEST_USER_PASSWORD", "")
REDIRECT_URIS = [u.strip() for u in os.environ.get("REDIRECT_URIS", "").split(",") if u.strip()]
if not REDIRECT_URIS or "*" in REDIRECT_URIS:
    sys.exit("REDIRECT_URIS must be an explicit, non-wildcard comma-separated list "
             "(refusing '*' — set sso_redirect_uris in the env overlay).")
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
            loc = resp.headers.get("Location", "")
            return resp.status, (json.loads(raw) if raw.strip() else {}), loc
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
    if not TEST_PW:
        sys.exit("ERROR: KC_TEST_USER_PASSWORD is required (set it in .env).")
    tok = admin_token()

    # 1) realm
    st, _, _ = req("GET", f"/admin/realms/{REALM}", token=tok)
    if st == 404:
        st, _, _ = req("POST", "/admin/realms", token=tok, body={"realm": REALM, "enabled": True})
        print(f"realm '{REALM}': created" if st in (201, 204) else f"realm create HTTP {st}")
    else:
        print(f"realm '{REALM}': exists")

    # 1b) allow unmanaged custom attributes. Keycloak 26's declarative user profile
    # silently DROPS undeclared attributes (e.g. tenant_id), so the mapper would map
    # nothing. ENABLED lets arbitrary attributes be stored + returned.
    st, prof, _ = req("GET", f"/admin/realms/{REALM}/users/profile", token=tok)
    if isinstance(prof, dict) and prof.get("unmanagedAttributePolicy") != "ENABLED":
        prof["unmanagedAttributePolicy"] = "ENABLED"
        req("PUT", f"/admin/realms/{REALM}/users/profile", token=tok, body=prof)
        print("user profile: unmanaged attributes ENABLED")
    else:
        print("user profile: unmanaged attributes already enabled")

    # 2) client (confidential; standard flow for the browser code flow, direct grants for headless tokens)
    st, clients, _ = req("GET", f"/admin/realms/{REALM}/clients?clientId={urllib.parse.quote(CLIENT_ID)}", token=tok)
    desired = {
        "clientId": CLIENT_ID, "enabled": True, "protocol": "openid-connect",
        "publicClient": False, "standardFlowEnabled": True, "directAccessGrantsEnabled": True,
        "serviceAccountsEnabled": False, "redirectUris": REDIRECT_URIS, "webOrigins": ["+"],
    }
    if clients:
        cuid = clients[0]["id"]
        req("PUT", f"/admin/realms/{REALM}/clients/{cuid}", token=tok, body={**clients[0], **desired})
        print(f"client '{CLIENT_ID}': exists (updated)")
    else:
        st, _, loc = req("POST", f"/admin/realms/{REALM}/clients", token=tok, body=desired)
        cuid = loc.rstrip("/").split("/")[-1]
        print(f"client '{CLIENT_ID}': created" if st in (201, 204) else f"client create HTTP {st}")

    # 3) tenant_id protocol mapper (user attribute -> access token + introspection claim)
    st, mappers, _ = req("GET", f"/admin/realms/{REALM}/clients/{cuid}/protocol-mappers/models", token=tok)
    have = {m["name"] for m in (mappers or [])}
    for name, attr in (("tenant_id", "tenant_id"), ("user_id", "user_id")):
        if name in have:
            print(f"mapper '{name}': exists")
            continue
        mapper = {
            "name": name, "protocol": "openid-connect",
            "protocolMapper": "oidc-usermodel-attribute-mapper",
            "config": {
                "user.attribute": attr, "claim.name": name, "jsonType.label": "String",
                "id.token.claim": "true", "access.token.claim": "true",
                "userinfo.token.claim": "true", "introspection.token.claim": "true",
            },
        }
        st, _, _ = req("POST", f"/admin/realms/{REALM}/clients/{cuid}/protocol-mappers/models", token=tok, body=mapper)
        print(f"mapper '{name}': created" if st in (201, 204) else f"mapper '{name}' HTTP {st}")

    # 3b) audience mapper: Keycloak 24+ introspection returns active:false unless the
    # introspecting client is in the token's aud. Add CLIENT_ID to the audience.
    aud_name = f"aud-{CLIENT_ID}"
    if aud_name in have:
        print(f"mapper '{aud_name}': exists")
    else:
        aud = {"name": aud_name, "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
               "config": {"included.client.audience": CLIENT_ID, "access.token.claim": "true",
                          "id.token.claim": "false", "introspection.token.claim": "true"}}
        st, _, _ = req("POST", f"/admin/realms/{REALM}/clients/{cuid}/protocol-mappers/models", token=tok, body=aud)
        print(f"mapper '{aud_name}': created" if st in (201, 204) else f"mapper '{aud_name}' HTTP {st}")

    # 4) client secret
    st, sec, _ = req("GET", f"/admin/realms/{REALM}/clients/{cuid}/client-secret", token=tok)
    secret = sec.get("value", "")
    if not secret:
        st, sec, _ = req("POST", f"/admin/realms/{REALM}/clients/{cuid}/client-secret", token=tok)
        secret = sec.get("value", "")

    # 5) test user + tenant_id attribute + password
    st, users, _ = req("GET", f"/admin/realms/{REALM}/users?username={urllib.parse.quote(TEST_USER)}&exact=true", token=tok)
    # email + first/last name are required or Keycloak 26 rejects the grant with
    # "Account is not fully set up"; requiredActions cleared so the user can log in.
    user_body = {"username": TEST_USER, "enabled": True, "emailVerified": True,
                 "email": f"{TEST_USER}@example.com", "firstName": "C360", "lastName": "Admin",
                 "requiredActions": [], "attributes": {"tenant_id": [TENANT_ID]}}
    if users:
        uid = users[0]["id"]
        req("PUT", f"/admin/realms/{REALM}/users/{uid}", token=tok, body={**users[0], **user_body})
        print(f"user '{TEST_USER}': exists (updated)")
    else:
        st, _, loc = req("POST", f"/admin/realms/{REALM}/users", token=tok, body=user_body)
        uid = loc.rstrip("/").split("/")[-1]
        print(f"user '{TEST_USER}': created" if st in (201, 204) else f"user create HTTP {st}")
    req("PUT", f"/admin/realms/{REALM}/users/{uid}/reset-password", token=tok,
        body={"type": "password", "value": TEST_PW, "temporary": False})
    print(f"user '{TEST_USER}': password set")

    # 6) write the secret back into .env (never print it)
    if secret:
        lines, seen = [], False
        if os.path.exists(ENV_FILE):
            with open(ENV_FILE, encoding="utf-8") as f:
                for ln in f:
                    if ln.startswith("KEYCLOAK_CLIENT_SECRET="):
                        lines.append(f"KEYCLOAK_CLIENT_SECRET={secret}\n"); seen = True
                    else:
                        lines.append(ln)
        if not seen:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(f"KEYCLOAK_CLIENT_SECRET={secret}\n")
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)
        print(f"client secret: written to {os.path.basename(ENV_FILE)} (KEYCLOAK_CLIENT_SECRET)")
    else:
        print("WARNING: could not read client secret")

    print(f"\nDONE. realm={REALM} client={CLIENT_ID} test_user={TEST_USER} tenant_id={TENANT_ID}")


if __name__ == "__main__":
    main()
