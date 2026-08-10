"""Keycloak-based authentication middleware for the Customer 360 API.

Also resolves the caller's ``tenant_id`` / ``user_id`` (sys_tenant.tenant_id
/ sys_user.user_id) onto ``request.state`` so ``core.database.get_db`` can
set the ``app.tenant_id`` / ``app.user_id`` Postgres session variables that
the tenant_policy Row-Level Security policies rely on (see the "ROW LEVEL
SECURITY" section of core-customer360/database-schema.sql).
"""

import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from core.cache import get_redis_client
from core.config import settings
from core.utils.security import decode_dev_access_token

logger = logging.getLogger(__name__)

EXEMPT_PATHS = {
    "/health",
    # GET /metadata is part of the login flow itself: the login screen calls
    # it (unauthenticated) to learn sso_login/sso_config and decide whether
    # to render the Keycloak button or the dev credential form (see
    # frontend-admin/static/js/auth-view.js). Every OTHER /metadata/* route
    # (dagster/domains/data-sources) is real protected API data with no
    # pre-login need and must NOT be exempt.
    "/api/v1/metadata",
    # Auth endpoints are the front door -- callers by definition have no
    # bearer token yet when hitting them (see core/routers/auth_api.py).
    "/api/v1/auth/login",
    "/api/v1/auth/callback",
    "/api/v1/auth/logout",
}
SSO_LOGIN=settings.sso_login
# TTL for the resolved (tenant_id, user_id) identity cache, independent of
# the Keycloak token TTL -- keeps a sys_user lookup off the hot path without
# staying stale for too long if a user's tenant/role changes.
IDENTITY_CACHE_TTL_SECONDS = 300


def _build_introspection_url() -> str:
    base_url = settings.sso_login_url.rstrip("/")
    return (
        f"{base_url}/realms/{settings.keycloak_realm}/protocol/openid-connect/token/introspect"
    )


def _introspect_with_keycloak(token: str) -> Optional[dict[str, Any]]:
    """Validate a bearer token against Keycloak and return the introspection payload."""
    url = _build_introspection_url()
    body = urllib.parse.urlencode(
        {
            "token": token,
            "client_id": settings.keycloak_client_id,
            "client_secret": settings.keycloak_client_secret,
            "token_type_hint": "access_token",
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    context = None
    if not settings.keycloak_verify_ssl:
        context = ssl._create_unverified_context()

    try:
        with urllib.request.urlopen(req, timeout=5, context=context) as response:
            payload = json.load(response)
            return payload if isinstance(payload, dict) else None
    except urllib.error.HTTPError as exc:
        logger.warning("Keycloak introspection failed with HTTP %s", exc.code)
        return None
    except Exception:
        logger.warning("Keycloak introspection request failed", exc_info=True)
        return None


def _cache_token(token: str, payload: dict[str, Any]) -> None:
    client = get_redis_client()
    if client is None:
        return

    exp = payload.get("exp")
    ttl_seconds: Optional[int] = None
    if isinstance(exp, (int, float)):
        ttl_seconds = max(60, int(exp) - int(time.time()))
    if ttl_seconds is not None and ttl_seconds > 0:
        try:
            client.set(
                f"auth:token:{token}",
                json.dumps(payload, default=str),
                ex=ttl_seconds,
            )
        except Exception:
            logger.warning("Failed to cache Keycloak token in Redis", exc_info=True)


def _load_cached_token(token: str) -> Optional[dict[str, Any]]:
    client = get_redis_client()
    if client is None:
        return None

    try:
        raw = client.get(f"auth:token:{token}")
    except Exception:
        logger.warning("Failed to read cached token from Redis", exc_info=True)
        return None

    if not raw:
        return None

    try:
        return json.loads(raw)
    except Exception:
        logger.warning("Cached token payload was not valid JSON", exc_info=True)
        return None


def _load_cached_identity(provider_subject_id: str, tenant_id: str) -> Optional[dict[str, str]]:
    """Load cached user identity by provider subject ID and tenant."""
    client = get_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(f"auth:identity:{tenant_id}:{provider_subject_id}")
    except Exception:
        logger.warning("Failed to read cached identity from Redis", exc_info=True)
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        logger.warning("Cached identity payload was not valid JSON", exc_info=True)
        return None


def _cache_identity(provider_subject_id: str, tenant_id: str, identity: dict[str, str]) -> None:
    """Cache user identity by provider subject ID and tenant."""
    client = get_redis_client()
    if client is None:
        return
    try:
        client.set(f"auth:identity:{tenant_id}:{provider_subject_id}", json.dumps(identity), ex=IDENTITY_CACHE_TTL_SECONDS)
    except Exception:
        logger.warning("Failed to cache resolved identity in Redis", exc_info=True)


def _get_or_create_user_on_login(payload: dict[str, Any]) -> Optional[dict[str, str]]:
    """Provisions/updates sys_user and sys_userinfo on a successful Keycloak login.
    
    Looks up via sys_userinfo table using auth_provider='KEYCLOAK' and 
    provider_subject_id from the token's ``sub`` claim.

    - **Existing user**: stamps ``last_login_at = now()`` on both sys_user and 
      sys_userinfo, returns ``(user_id, tenant_id)``.
    - **First-ever login for this identity**: auto-provisions both sys_user and 
      sys_userinfo rows. This REQUIRES the token to carry a ``tenant_id`` custom 
      claim (published via a Keycloak protocol mapper) identifying which tenant
      this identity belongs to -- without it we refuse to provision (fail
      closed: the caller gets no tenant context / no RLS-visible rows,
      rather than being silently assigned to the wrong tenant).

    Local import of SessionLocal avoids a hard import-time dependency
    between core.auth and core.database.
    """
    provider_subject_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    
    if not provider_subject_id or not tenant_id:
        if provider_subject_id and not tenant_id:
            logger.warning(
                "Cannot auto-provision sys_user for provider_subject_id=%s: token has no tenant_id claim",
                provider_subject_id,
            )
        return None

    from core.database import SessionLocal

    db = SessionLocal()
    try:
        # Look up existing user via sys_userinfo (Keycloak provider)
        row = db.execute(
            text(
                f"""
                SELECT u.user_id, u.tenant_id 
                FROM {settings.db_schema}.sys_userinfo ui
                JOIN {settings.db_schema}.sys_user u ON ui.user_id = u.user_id
                WHERE ui.tenant_id = :tenant_id 
                  AND ui.auth_provider = 'KEYCLOAK'
                  AND ui.provider_subject_id = :provider_subject_id
                """
            ),
            {"tenant_id": tenant_id, "provider_subject_id": provider_subject_id},
        ).mappings().first()

        if row is not None:
            # Existing user: refresh last_login_at on both tables
            db.execute(
                text(f"UPDATE {settings.db_schema}.sys_user SET last_login_at = now() WHERE user_id = :uid"),
                {"uid": row["user_id"]},
            )
            db.execute(
                text(
                    f"""
                    UPDATE {settings.db_schema}.sys_userinfo 
                    SET last_login_at = now() 
                    WHERE tenant_id = :tenant_id 
                      AND auth_provider = 'KEYCLOAK'
                      AND provider_subject_id = :provider_subject_id
                    """
                ),
                {"tenant_id": tenant_id, "provider_subject_id": provider_subject_id},
            )
            db.commit()
            return {"user_id": str(row["user_id"]), "tenant_id": str(row["tenant_id"])}

        # New identity: auto-provision both sys_user and sys_userinfo
        username = payload.get("preferred_username") or payload.get("email") or provider_subject_id
        email = payload.get("email")
        full_name = payload.get("name")

        # Insert into sys_user first
        inserted_user = db.execute(
            text(
                f"""
                INSERT INTO {settings.db_schema}.sys_user
                    (tenant_id, username, email, full_name, last_login_at)
                VALUES (:tenant_id, :username, :email, :full_name, now())
                RETURNING user_id, tenant_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "username": username,
                "email": email,
                "full_name": full_name,
            },
        ).mappings().first()

        if inserted_user is None:
            db.rollback()
            return None

        user_id = inserted_user["user_id"]

        # Insert into sys_userinfo to link the Keycloak identity
        db.execute(
            text(
                f"""
                INSERT INTO {settings.db_schema}.sys_userinfo
                    (tenant_id, user_id, auth_provider, provider_subject_id, last_login_at)
                VALUES (:tenant_id, :user_id, 'KEYCLOAK', :provider_subject_id, now())
                """
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "provider_subject_id": provider_subject_id,
            },
        )
        db.commit()
        return {"user_id": str(user_id), "tenant_id": str(inserted_user["tenant_id"])}

    except Exception:
        db.rollback()
        logger.warning("Failed to get-or-create sys_user on Keycloak login", exc_info=True)
        return None
    finally:
        db.close()


def _resolve_tenant_and_user(payload: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Resolves (tenant_id, user_id) for an authenticated request.

    Prefers explicit ``tenant_id``/``user_id`` custom claims on the Keycloak
    token (if a protocol mapper publishes them); otherwise falls back to a
    (Redis-cached) ``sys_userinfo`` get-or-create keyed by the standard ``sub``
    claim and tenant_id -- see ``_get_or_create_user_on_login``. Caching means 
    last_login_at is refreshed at most once per IDENTITY_CACHE_TTL_SECONDS per 
    user, not on every single request.
    
    Returns (None, None) if tenant_id claim is missing or user can't be resolved
    (fail-closed principle: better to deny access than grant with incomplete identity).
    """
    tenant_id = payload.get("tenant_id")
    user_id = payload.get("user_id")
    
    # Explicit claims in token short-circuit everything
    if tenant_id and user_id:
        return tenant_id, user_id

    # Fail-closed: must have tenant_id from claims or provider_subject_id lookup
    provider_subject_id = payload.get("sub")
    if not provider_subject_id or not tenant_id:
        return None, None

    # Try to resolve user identity from cache or database
    identity = _load_cached_identity(provider_subject_id, tenant_id)
    if identity is None:
        identity = _get_or_create_user_on_login(payload)
        if identity is not None:
            _cache_identity(provider_subject_id, tenant_id, identity)

    # Only return identity if we successfully resolved it
    if identity is not None:
        return identity.get("tenant_id"), identity.get("user_id")
    
    # Fail-closed: return (None, None) if user can't be resolved
    return None, None


def _normalize_path(path: str, root_path: str = "") -> str:
    """Normalize a request path for exempt-path matching.

    The frontend may request /api/v1/metadata with or without a trailing slash,
    and the app may also be mounted under a root path such as /c360api.
    We strip the configured root path and then treat both slash variants as the
    same public login endpoint.
    """
    if root_path and path.startswith(root_path):
        path = path[len(root_path):] or "/"
    return path.rstrip("/") or "/"


def _apply_dev_tenant_headers(request: Request) -> None:
    """Dev/test convenience only, for EXEMPT_PATHS: when SSO_LOGIN is
    disabled, trust X-Tenant-Id/X-User-Id headers so the app.tenant_id RLS
    session variable (see core/database.py) is available even on the small
    set of unauthenticated routes. NEVER used on protected routes -- those
    always require a valid token (dev JWT or Keycloak) regardless of
    SSO_LOGIN, see auth_middleware."""
    tenant_id = request.headers.get("X-Tenant-Id")
    user_id = request.headers.get("X-User-Id")
    if tenant_id:
        request.state.tenant_id = tenant_id
    if user_id:
        request.state.user_id = user_id


def _unauthorized_response(request: Request, detail: str) -> JSONResponse:
    """Return a 401 JSON response that still includes CORS headers.

    Browsers enforce CORS checks before exposing response details. If auth
    rejects a cross-origin request without CORS headers, frontend callers get a
    generic CORS error instead of the real 401 payload.
    """
    headers: dict[str, str] = {}
    if request.headers.get("origin"):
        headers["Access-Control-Allow-Origin"] = "*"
    return JSONResponse(status_code=401, content={"detail": detail}, headers=headers)


async def auth_middleware(request: Request, call_next):
    """Ensure API requests present a valid bearer token before continuing.

    Every route not in EXEMPT_PATHS (health/root-metadata/login/callback/
    logout) requires a token, in both modes -- there is no "no token at all"
    bypass anymore, in either mode:
      - SSO_LOGIN=true: token must be a real Keycloak access token (verified
        via introspection).
      - SSO_LOGIN=false: token must be a locally-signed dev JWT obtained from
        POST /auth/login (see core.utils.security) -- same
        Authorization: Bearer contract used in production, just HS256-signed
        locally instead of by Keycloak.
    """
    if request.method == "OPTIONS":
        return await call_next(request)

    normalized_path = _normalize_path(
        request.url.path,
        root_path=request.scope.get("root_path", ""),
    )
    if normalized_path in {_normalize_path(path) for path in EXEMPT_PATHS}:
        _apply_dev_tenant_headers(request)
        return await call_next(request)

    authorization = request.headers.get("Authorization", "")
    token = authorization[len("Bearer "):].strip() if authorization.startswith("Bearer ") else ""

    if not token:
        return _unauthorized_response(request, "Authentication required")

    if not SSO_LOGIN:
        payload = decode_dev_access_token(token)
        if payload is None:
            return _unauthorized_response(request, "Invalid or expired dev token")

        request.state.user = payload
        request.state.token = token
        tenant_id, user_id = _resolve_tenant_and_user(payload)
        if tenant_id:
            request.state.tenant_id = tenant_id
        if user_id:
            request.state.user_id = user_id
        return await call_next(request)

    payload = _load_cached_token(token)
    if payload is None:
        payload = _introspect_with_keycloak(token)
        if not payload or not payload.get("active"):
            return _unauthorized_response(request, "Invalid or expired token")
        _cache_token(token, payload)

    request.state.user = payload
    request.state.token = token

    tenant_id, user_id = _resolve_tenant_and_user(payload)
    if tenant_id:
        request.state.tenant_id = tenant_id
    if user_id:
        request.state.user_id = user_id

    return await call_next(request)
