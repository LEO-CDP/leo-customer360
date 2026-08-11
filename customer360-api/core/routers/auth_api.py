"""API router for authentication: dev-mode credential login (SSO_LOGIN=false),
Keycloak Authorization Code exchange + logout (SSO_LOGIN=true).

Kept separate from core.auth (the request middleware) because these are
regular request/response endpoints, not the per-request token-verification
hook. All three routes are added to ``core.auth.EXEMPT_PATHS`` since a caller
by definition has no bearer token yet when hitting them.
"""

import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from core.config import settings
from core.database import SessionLocal
from core.repositories.metadata_repository import DEFAULT_TENANT_ID
from core.repositories.user_repository import UserRepository
from core.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    LogoutResponse,
    SsoCallbackRequest,
    SsoTokenResponse,
)
from core.utils.security import create_dev_access_token, verify_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


def _token_endpoint() -> str:
    base_url = settings.sso_login_url.rstrip("/")
    return f"{base_url}/realms/{settings.keycloak_realm}/protocol/openid-connect/token"


def _end_session_endpoint() -> str:
    base_url = settings.sso_login_url.rstrip("/")
    return f"{base_url}/realms/{settings.keycloak_realm}/protocol/openid-connect/logout"


def _issue_token(tenant_id: UUID, user_id: Optional[UUID], username: str, roles: list[str]) -> dict[str, Any]:
    """Builds the ``access_token``/``token_type``/``expires_in`` fields of
    ``LoginResponse`` for a resolved dev-mode identity."""
    token, expires_in = create_dev_access_token(
        tenant_id=str(tenant_id),
        user_id=str(user_id) if user_id else None,
        username=username,
        roles=roles,
    )
    return {"access_token": token, "token_type": "Bearer", "expires_in": expires_in}


def _exchange_code_for_token(code: str, redirect_uri: str) -> dict[str, Any]:
    """Swaps an authorization code for tokens using the confidential client
    secret (never exposed to the browser -- see metadata_repository.get_system_metadata)."""
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": settings.keycloak_client_id,
            "client_secret": settings.keycloak_client_secret,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        _token_endpoint(),
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    context = None if settings.keycloak_verify_ssl else ssl._create_unverified_context()

    try:
        with urllib.request.urlopen(req, timeout=10, context=context) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        logger.warning("Keycloak token exchange failed with HTTP %s: %s", exc.code, detail)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not exchange authorization code with Keycloak",
        ) from exc
    except Exception as exc:
        logger.warning("Keycloak token exchange request failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Keycloak token endpoint unreachable",
        ) from exc


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest) -> Any:
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
            root_user = UserRepository(db).get_user_by_username(username, tenant_id)
            if root_user:
                root_user_id = root_user.user_id
                db.execute(
                    text(f"UPDATE {settings.db_schema}.sys_user SET last_login_at = now() WHERE user_id = :uid"),
                    {"uid": root_user_id},
                )
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
        repo = UserRepository(db)
        user = repo.get_user_by_username(username, tenant_id)
        password_hash = repo.get_local_password_hash(user.user_id, tenant_id) if user else None
        if not user or not password_hash or not verify_password(payload.password, password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )

        db.execute(
            text(f"UPDATE {settings.db_schema}.sys_user SET last_login_at = now() WHERE user_id = :uid"),
            {"uid": user.user_id},
        )
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


all_auth_routers = [router]
