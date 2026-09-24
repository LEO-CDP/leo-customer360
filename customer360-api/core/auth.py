"""Authentication middleware, identity resolution, and auth dependencies."""

import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Optional

from fastapi import HTTPException, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from core.cache import get_redis_client
from leo_customer360_dao.config import settings
from leo_customer360_dao.repositories.auth_repository import AuthRepository
from core.utils.rate_limiter import RedisRateLimiter
from leo_customer360_dao.utils.security import decode_dev_access_token

logger = logging.getLogger(__name__)

# Public paths that do not require a bearer token.
EXEMPT_PATHS = {
    "/health",
    "/api/v1/metadata",
    "/api/v1/auth/login",
    "/api/v1/auth/callback",
    "/api/v1/auth/logout",
    "/api/v1/auth/zalo-redirect",
}

# Runtime auth configuration.
SSO_LOGIN = settings.sso_login
IDENTITY_CACHE_TTL_SECONDS = 200
TENANT_ADMIN_ROLES = {"platform_admin", "super_admin", "system_admin", "tenant_admin", "admin"}
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

# Failed-auth rate limiter, keyed by client IP.
_failed_auth_rate_limiter = RedisRateLimiter(
    max_attempts=settings.auth_rate_limit_max_attempts,
    window_seconds=settings.auth_rate_limit_window_seconds,
)


def _client_ip(request: Request) -> str:
    """Return the request client address for rate-limit keys."""
    return request.client.host if request.client else "unknown"


def _build_introspection_url() -> str:
    """Build the Keycloak token-introspection endpoint URL."""
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
    """Cache a Keycloak introspection payload until its token expires."""
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
    """Load a cached Keycloak introspection payload, if valid."""
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
        payload = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        return json.loads(payload)
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
        identity = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        return json.loads(identity)
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


def _pin_transaction_tenant(db: Any, tenant_id: str) -> None:
    """Set the transaction-local tenant GUC before RLS-protected provisioning."""
    connection = getattr(db, "connection", None)
    if connection is None:
        return
    from sqlalchemy import text

    connection().execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})


def _get_or_create_user_on_login(payload: dict[str, Any]) -> Optional[dict[str, str]]:
    """Resolve or provision the Keycloak user for a token identity.

    Provisioning requires the token's tenant claim; missing identity data fails
    closed. The local session import avoids an auth/database import cycle.
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
        # Pin the tenant before the RLS-protected lookup/insert.
        _pin_transaction_tenant(db, str(tenant_id))
        repo = AuthRepository(db)
        result = repo.get_or_create_keycloak_user(tenant_id, payload, provider_subject_id)
        if result is None:
            db.rollback()
            return None

        db.commit()
        return result
    except Exception:
        db.rollback()
        logger.warning("Failed to get-or-create sys_user on Keycloak login", exc_info=True)
        return None
    finally:
        db.close()


def _resolve_tenant_and_user(payload: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Resolve tenant and user IDs from token claims or cached provisioning."""
    tenant_id = payload.get("tenant_id")
    user_id = payload.get("user_id")
    
    # Explicit claims avoid a database lookup.
    if tenant_id and user_id:
        return tenant_id, user_id

    # A tenant claim is required for fallback provisioning.
    provider_subject_id = payload.get("sub")
    if not provider_subject_id or not tenant_id:
        return None, None

    # Prefer the cached identity, then provision/read from the database.
    identity = _load_cached_identity(provider_subject_id, tenant_id)
    if identity is None:
        identity = _get_or_create_user_on_login(payload)
        if identity is not None:
            _cache_identity(provider_subject_id, tenant_id, identity)

    if identity is not None:
        return identity.get("tenant_id"), identity.get("user_id")

    # Fail closed when identity resolution fails.
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
    """Apply tenant/user headers only on exempt routes when SSO is disabled."""
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
    """Require a valid Keycloak or locally signed bearer token."""
    if request.method == "OPTIONS":
        return await call_next(request)

    normalized_path = _normalize_path(
        request.url.path,
        root_path=request.scope.get("root_path", ""),
    )
    if normalized_path in {_normalize_path(path) for path in EXEMPT_PATHS}:
        _apply_dev_tenant_headers(request)
        return await call_next(request)

    if _failed_auth_rate_limiter.is_blocked(f"auth-fail:{_client_ip(request)}"):
        return _unauthorized_response(request, "Too many failed authentication attempts. Try again later.")

    authorization = request.headers.get("Authorization", "")
    token = authorization[len("Bearer "):].strip() if authorization.startswith("Bearer ") else ""

    if not token:
        _failed_auth_rate_limiter.record_failure(f"auth-fail:{_client_ip(request)}")
        return _unauthorized_response(request, "Authentication required")

    if not SSO_LOGIN:
        payload = decode_dev_access_token(token)
        if payload is None:
            _failed_auth_rate_limiter.record_failure(f"auth-fail:{_client_ip(request)}")
            return _unauthorized_response(request, "Invalid or expired dev token")

        request.state.user = payload
        request.state.token = token
        tenant_id, user_id = _resolve_tenant_and_user(payload)
        if not tenant_id:
            return _unauthorized_response(request, "Tenant context could not be resolved")
        request.state.tenant_id = tenant_id
        if user_id:
            request.state.user_id = user_id
        return await call_next(request)

    payload = _load_cached_token(token)
    if payload is None:
        payload = _introspect_with_keycloak(token)
        if not payload or not payload.get("active"):
            _failed_auth_rate_limiter.record_failure(f"auth-fail:{_client_ip(request)}")
            return _unauthorized_response(request, "Invalid or expired token")
        _cache_token(token, payload)

    request.state.user = payload
    request.state.token = token

    tenant_id, user_id = _resolve_tenant_and_user(payload)
    if not tenant_id:
        return _unauthorized_response(request, "Tenant context could not be resolved")
    request.state.tenant_id = tenant_id
    if user_id:
        request.state.user_id = user_id

    return await call_next(request)


def get_current_roles(request: Request) -> list[str]:
    """Collect roles from dev-JWT, realm, and client Keycloak claims."""
    payload = getattr(request.state, "user", None)
    if not isinstance(payload, dict):
        return []

    roles: list[str] = list(payload.get("roles") or [])

    realm_access = payload.get("realm_access")
    if isinstance(realm_access, dict) and isinstance(realm_access.get("roles"), list):
        roles.extend(realm_access["roles"])

    resource_access = payload.get("resource_access")
    if isinstance(resource_access, dict):
        for client in resource_access.values():
            if isinstance(client, dict) and isinstance(client.get("roles"), list):
                roles.extend(client["roles"])

    return roles


def require_admin(request: Request) -> None:
    """Require the admin role when SSO is enabled."""
    if not SSO_LOGIN:
        return

    roles = get_current_roles(request)
    if not roles and getattr(request.state, "user", None) is None:
        return

    if "admin" not in {r.lower() for r in roles}:
        raise HTTPException(status_code=403, detail="This action requires the 'admin' role.")


def require_tenant(request: Request) -> str:
    """Return the caller's tenant_id (set by auth_middleware), normalized +
    validated as a UUID, or raise 400 -- a malformed value must not 500 a later
    ``uuid.UUID()`` cast."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context found (missing X-Tenant-Id)")
    try:
        return str(uuid.UUID(str(tenant_id)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="X-Tenant-Id is not a valid UUID") from exc


def require_tenant_admin(request: Request, action: str = "this action") -> None:
    """Require an authenticated tenant-admin role when SSO is enabled."""
    if not SSO_LOGIN:
        return
    if not isinstance(getattr(request.state, "user", None), dict):
        raise HTTPException(status_code=401, detail="Authentication required")
    if not {r.lower() for r in get_current_roles(request)} & TENANT_ADMIN_ROLES:
        raise HTTPException(status_code=403, detail=f"Tenant admin role required for {action}")


def resolve_mcp_tenant_id(api_key: str) -> str:
    """Resolve a tenant ID from the Redis ``apikey:{key}`` mapping."""
    redis_client = get_redis_client()
    if redis_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP authentication backend unavailable"
        )

    try:
        raw_tenant_id = redis_client.get(f"apikey:{api_key}")
    except Exception:
        logger.warning("Failed to verify MCP API key from Redis", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify MCP API key from Redis"
        )

    if raw_tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing MCP API Key"
        )

    if isinstance(raw_tenant_id, bytes):
        tenant_id = raw_tenant_id.decode("utf-8", errors="ignore").strip()
    else:
        tenant_id = str(raw_tenant_id).strip()

    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid MCP API key tenant mapping"
        )

    return tenant_id

async def verify_mcp_api_key(api_key: str = Security(api_key_header)):
    """Validate an MCP API key and return its mapped tenant ID."""
    return resolve_mcp_tenant_id(api_key)