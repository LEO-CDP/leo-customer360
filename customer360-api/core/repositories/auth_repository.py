"""API-layer authentication orchestration facade."""

import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text

from core.utils.rate_limiter import RedisRateLimiter
from leo_customer360_dao.config import settings
from leo_customer360_dao.utils.security import create_dev_access_token

logger = logging.getLogger(__name__)


class AuthRepository:
    """Own token, login metadata, and external OAuth coordination."""

    def __init__(self, db=None, user_repository_cls=None):
        """Create an auth facade with an optional session and user class."""
        self.db = db
        self.user_repository_cls = user_repository_cls
        self.rate_limiter = RedisRateLimiter(
            max_attempts=settings.auth_rate_limit_max_attempts,
            window_seconds=settings.auth_rate_limit_window_seconds,
        )

    def login_rate_limit_key(self, request, username):
        """Build the username and client-IP login throttle key."""
        client_ip = request.client.host if request.client else "unknown"
        return f"login:{client_ip}:{username}"

    def issue_token(self, tenant_id: UUID, user_id: Optional[UUID], username: str, roles: list[str]) -> dict[str, Any]:
        """Issue a development access token response payload."""
        token, expires_in = create_dev_access_token(
            tenant_id=str(tenant_id), user_id=str(user_id) if user_id else None, username=username, roles=roles
        )
        return {"access_token": token, "token_type": "Bearer", "expires_in": expires_in}

    def token_endpoint(self):
        """Return the Keycloak authorization-code token endpoint."""
        return f"{settings.sso_login_url.rstrip('/')}/realms/{settings.keycloak_realm}/protocol/openid-connect/token"

    def end_session_endpoint(self):
        """Return the Keycloak logout endpoint."""
        return f"{settings.sso_login_url.rstrip('/')}/realms/{settings.keycloak_realm}/protocol/openid-connect/logout"

    def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange an authorization code with Keycloak."""
        body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "client_id": settings.keycloak_client_id, "client_secret": settings.keycloak_client_secret}).encode("utf-8")
        request = urllib.request.Request(self.token_endpoint(), data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        context = None if settings.keycloak_verify_ssl else ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(request, timeout=10, context=context) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            logger.warning("Keycloak token exchange failed with HTTP %s", exc.code)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not exchange authorization code with Keycloak") from exc
        except Exception as exc:
            logger.warning("Keycloak token exchange request failed", exc_info=True)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Keycloak token endpoint unreachable") from exc

    def user_repository(self, db):
        """Create the configured DAO user repository for a session."""
        if self.user_repository_cls is None:
            from leo_customer360_dao.repositories.user_repository import UserRepository

            self.user_repository_cls = UserRepository
        return self.user_repository_cls(db)

    def get_login_user(self, db, username: str, tenant_id: UUID, include_password: bool = True):
        """Load a local-login user and optionally its password hash for a tenant."""
        repository = self.user_repository(db)
        user = repository.get_user_by_username(username, tenant_id)
        password_hash = (
            repository.get_local_password_hash(user.user_id, tenant_id)
            if user and include_password
            else None
        )
        return user, password_hash

    def update_last_login(self, db, user_id: UUID) -> None:
        """Stamp the last successful local login for a user."""
        db.execute(
            text(f"UPDATE {settings.db_schema}.sys_user SET last_login_at = now() WHERE user_id = :uid"),
            {"uid": user_id},
        )

    def get_or_create_keycloak_user(
        self, tenant_id: str, payload: dict[str, Any], provider_subject_id: str
    ) -> Optional[dict[str, str]]:
        """Resolve or provision a tenant-scoped Keycloak identity."""
        username = payload.get("preferred_username") or payload.get("email") or provider_subject_id
        email = payload.get("email")
        full_name = payload.get("name")
        schema = settings.db_schema
        if self.db is None:
            raise RuntimeError("A database session is required for Keycloak user resolution")
        existing = self._mapping_first(
            self.db.execute(
                text(
                    f"SELECT u.user_id, u.tenant_id FROM {schema}.sys_userinfo ui "
                    f"JOIN {schema}.sys_user u ON u.user_id = ui.user_id "
                    "WHERE ui.auth_provider = 'KEYCLOAK' "
                    "AND ui.provider_subject_id = :provider_subject_id "
                    "AND ui.tenant_id = :tenant_id AND ui.status = 'ACTIVE' LIMIT 1"
                ),
                {"provider_subject_id": provider_subject_id, "tenant_id": tenant_id},
            )
        )
        if existing:
            self.db.execute(
                text(f"UPDATE {schema}.sys_user SET last_login_at = now() WHERE user_id = :uid"),
                {"uid": existing["user_id"]},
            )
            self.db.execute(
                text(
                    f"UPDATE {schema}.sys_userinfo SET last_login_at = now() "
                    "WHERE tenant_id = :tenant_id AND auth_provider = 'KEYCLOAK' "
                    "AND provider_subject_id = :provider_subject_id"
                ),
                {"tenant_id": tenant_id, "provider_subject_id": provider_subject_id},
            )
            return {"user_id": str(existing["user_id"]), "tenant_id": str(existing["tenant_id"])}

        created = self._mapping_first(
            self.db.execute(
                text(
                    f"INSERT INTO {schema}.sys_user "
                    "(tenant_id, username, email, full_name, status, last_login_at) "
                    "VALUES (:tenant_id, :username, :email, :full_name, 'ACTIVE', now()) "
                    "RETURNING user_id, tenant_id"
                ),
                {
                    "tenant_id": tenant_id,
                    "username": username,
                    "email": email,
                    "full_name": full_name,
                },
            )
        )
        if not created:
            return None
        self.db.execute(
            text(
                f"INSERT INTO {schema}.sys_userinfo "
                "(tenant_id, user_id, auth_provider, provider_subject_id, status, last_login_at) "
                "VALUES (:tenant_id, :user_id, 'KEYCLOAK', :provider_subject_id, 'ACTIVE', now())"
            ),
            {
                "tenant_id": tenant_id,
                "user_id": created["user_id"],
                "provider_subject_id": provider_subject_id,
            },
        )
        return {"user_id": str(created["user_id"]), "tenant_id": str(created["tenant_id"])}

    @staticmethod
    def _mapping_first(result) -> Optional[dict[str, Any]]:
        """Return the first mapping from a SQLAlchemy result-like object."""
        row = result.mappings().first()
        return dict(row) if row else None