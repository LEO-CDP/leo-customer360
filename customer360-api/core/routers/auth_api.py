"""API router for authentication: dev-mode credential login (SSO_LOGIN=false),
Keycloak Authorization Code exchange + logout (SSO_LOGIN=true).

Kept separate from core.auth (the request middleware) because these are
regular request/response endpoints, not the per-request token-verification
hook. All three routes are added to ``core.auth.EXEMPT_PATHS`` since a caller
by definition has no bearer token yet when hitting them.
"""

import logging
import urllib.parse
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import text

from leo_customer360_dao.config import settings
from leo_customer360_dao.crud import zalo_oa
from core.database import SessionLocal
from core.repositories.metadata_repository import DEFAULT_TENANT_ID
from core.repositories.zalo_repository import ZaloConnector
from core.repositories.auth_repository import AuthRepository
from leo_customer360_dao.schemas.crm import ZaloConnectResult
from leo_customer360_dao.repositories.user_repository import UserRepository
from leo_customer360_dao.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    LogoutResponse,
    SsoCallbackRequest,
    SsoTokenResponse,
)
from core.utils.rate_limiter import RedisRateLimiter
from leo_customer360_dao.utils.security import verify_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])

# Throttles repeated bad credentials against POST /auth/login, keyed by
# username+client IP so one attacker can't lock out another legitimate
# username, and one username isn't globally locked by a single abusive IP.
_login_rate_limiter = RedisRateLimiter(
    max_attempts=settings.auth_rate_limit_max_attempts,
    window_seconds=settings.auth_rate_limit_window_seconds,
)


def _login_rate_limit_key(request: Request, username: str) -> str:
    return AuthRepository().login_rate_limit_key(request, username)


def _token_endpoint() -> str:
    return AuthRepository().token_endpoint()


def _end_session_endpoint() -> str:
    return AuthRepository().end_session_endpoint()


def _issue_token(tenant_id: UUID, user_id: Optional[UUID], username: str, roles: list[str]) -> dict[str, Any]:
    """Builds the ``access_token``/``token_type``/``expires_in`` fields of
    ``LoginResponse`` for a resolved dev-mode identity."""
    return AuthRepository().issue_token(tenant_id, user_id, username, roles)


def _exchange_code_for_token(code: str, redirect_uri: str) -> dict[str, Any]:
    """Swaps an authorization code for tokens using the confidential client
    secret (never exposed to the browser -- see metadata_repository.get_system_metadata)."""
    return AuthRepository().exchange_code(code, redirect_uri)


def _auth_repository() -> AuthRepository:
    """Build an auth repository with the current router test seam."""
    return AuthRepository(user_repository_cls=UserRepository)


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request) -> Any:
    """Dev-mode credential login (only meaningful while SSO_LOGIN=false).

    Checks the single DEFAULT_ROOT_USERNAME/PASSWORD super-admin pair first,
    then falls back to a real ``sys_user`` row (created via the System Users
    admin screen) with a password set.
    """
    if settings.sso_login:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SSO_LOGIN is enabled; use the Keycloak sign-in flow instead",
        )

    tenant_id = payload.tenant_id or DEFAULT_TENANT_ID
    username = payload.username.strip().lower()

    rate_limit_key = _login_rate_limit_key(request, username)
    if _login_rate_limiter.is_blocked(rate_limit_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
        )

    if (
        settings.default_root_password
        and username == settings.default_root_username.strip().lower()
        and payload.password == settings.default_root_password
    ):
        # Seeded at startup (see init_core_data.seed_root_admin_user) so this
        # resolves to a real sys_user row -- without a real user_id, every
        # other endpoint's get_current_user dependency 401s for this login.
        db = SessionLocal()
        root_user_id: Optional[UUID] = None
        try:
            db.execute(text("SELECT set_config('app.tenant_id', :t_id, true)"), {"t_id": str(tenant_id)})
            root_user, _ = _auth_repository().get_login_user(db, username, tenant_id, include_password=False)
            if root_user:
                root_user_id = root_user.user_id
                _auth_repository().update_last_login(db, root_user_id)
                db.commit()
            else:
                logger.warning(
                    "Root admin '%s' has no sys_user row for tenant %s -- API calls needing get_current_user will 401. "
                    "Restart the API to re-run init_core_data seeding.",
                    username, tenant_id,
                )
        finally:
            db.close()

        return LoginResponse(
            user_id=root_user_id,
            tenant_id=tenant_id,
            username=settings.default_root_username,
            full_name="Root Administrator",
            roles=["root"],
            is_root=True,
            **_issue_token(tenant_id, root_user_id, settings.default_root_username, ["root"]),
        )

    db = SessionLocal()
    try:
        db.execute(text("SELECT set_config('app.tenant_id', :t_id, true)"), {"t_id": str(tenant_id)})
        auth_repo = _auth_repository()
        user, password_hash = auth_repo.get_login_user(db, username, tenant_id)
        if not user or not password_hash or not verify_password(payload.password, password_hash):
            _login_rate_limiter.record_failure(rate_limit_key)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )

        auth_repo.update_last_login(db, user.user_id)
        db.commit()

        return LoginResponse(
            user_id=user.user_id,
            tenant_id=tenant_id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            roles=["user"],
            is_root=False,
            **_issue_token(tenant_id, user.user_id, user.username, ["user"]),
        )
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/callback", response_model=SsoTokenResponse)
async def sso_callback(payload: SsoCallbackRequest) -> Any:
    """Exchanges a Keycloak authorization code for tokens (SSO_LOGIN=true)."""
    if not settings.sso_login:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SSO_LOGIN is disabled; use the dev credential login instead",
        )

    tokens = _exchange_code_for_token(payload.code, payload.redirect_uri)
    return SsoTokenResponse(
        access_token=tokens["access_token"],
        id_token=tokens.get("id_token"),
        refresh_token=tokens.get("refresh_token"),
        expires_in=tokens.get("expires_in"),
        token_type=tokens.get("token_type", "Bearer"),
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(payload: LogoutRequest) -> Any:
    """Dev mode: no server-side session to destroy (auth is header-based).
    SSO mode: hands back Keycloak's end-session URL for the browser to
    navigate to, invalidating the actual Keycloak session too."""
    if not settings.sso_login:
        return LogoutResponse(sso_login=False, logout_url=None)

    params: dict[str, str] = {"client_id": settings.keycloak_client_id}
    if payload.id_token_hint:
        params["id_token_hint"] = payload.id_token_hint
    if payload.post_logout_redirect_uri:
        params["post_logout_redirect_uri"] = payload.post_logout_redirect_uri

    logout_url = f"{_end_session_endpoint()}?{urllib.parse.urlencode(params)}"
    return LogoutResponse(sso_login=True, logout_url=logout_url)


@router.get("/zalo-redirect", response_model=ZaloConnectResult)
async def zalo_redirect(
    request: Request,
    oa_id: str = Query(..., description="Zalo Official Account id"),
    code: str = Query(..., description="OA authorization code returned by Zalo"),
    state: str = Query(..., description="Signed, tenant-bound state echoed by Zalo"),
) -> Any:
    """Public Zalo OA OAuth callback: verify the tenant-bound ``state``, exchange
    the code for access+refresh tokens, and upsert the tenant's Zalo
    ``crm_connector_config`` row. Exempt from bearer auth (the browser redirect carries no
    token) -- the signed ``state`` is what binds the call to a tenant."""
    db = SessionLocal()
    try:
        zalo_connector = ZaloConnector(db)
        tenant_id = zalo_connector.verify_state(state)
        db.info["tenant_id"] = tenant_id
        db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
        connector = zalo_connector.get_oa_config(UUID(tenant_id))
        if connector is None:
            raise zalo_oa.ZaloOAError("Zalo connector configuration is not active", status=503)
        tokens = zalo_connector.exchange_code(code, connector)
        zalo_connector.save_tokens(
            UUID(tenant_id),
            oa_id=oa_id,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token", ""),
            expires_in=int(tokens.get("expires_in", 3600)),
        )
    except zalo_oa.ZaloOAError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    finally:
        db.close()
    return ZaloConnectResult(status="connected", oa_id=oa_id)


all_auth_routers = [router]
