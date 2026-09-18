"""Pydantic schemas for the /auth endpoints (dev credential login, SSO code
exchange, logout)."""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Dev-mode login (SSO_LOGIN=false only)."""
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)
    tenant_id: Optional[UUID] = None


class LoginResponse(BaseModel):
    """Resolved identity + bearer token returned by POST /auth/login.

    ``access_token`` is a locally-signed dev JWT (see
    core.utils.security.create_dev_access_token) carrying the same
    ``tenant_id``/``user_id`` claims a real Keycloak token would -- send it
    as ``Authorization: Bearer <access_token>`` to call any protected
    endpoint exactly like a production (SSO_LOGIN=true) caller would. The
    ``user_id``/``tenant_id``/``roles`` fields are also returned directly for
    convenience (e.g. UI display) without decoding the token client-side.
    """
    user_id: Optional[UUID] = None
    tenant_id: UUID
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    roles: list[str] = ["user"]
    is_root: bool = False
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class SsoCallbackRequest(BaseModel):
    """Authorization Code exchange request (SSO_LOGIN=true)."""
    code: str = Field(..., min_length=1)
    redirect_uri: str = Field(..., min_length=1)


class SsoTokenResponse(BaseModel):
    """Tokens handed back to the frontend after a successful code exchange."""
    access_token: str
    id_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_in: Optional[int] = None
    token_type: str = "Bearer"


class LogoutRequest(BaseModel):
    id_token_hint: Optional[str] = None
    post_logout_redirect_uri: Optional[str] = None


class LogoutResponse(BaseModel):
    sso_login: bool
    logout_url: Optional[str] = None
