"""Repository for auth-related database operations.

Encapsulates the sys_user / sys_userinfo SQL used during Keycloak login
provisioning. This keeps database access out of core/auth.py and follows the
same OOP repository pattern used throughout customer360-api/core/repositories.
"""

import logging
import re
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import settings

logger = logging.getLogger(__name__)

# settings.db_schema is operator-controlled config (.env), never request
# input, so it isn't attacker-reachable -- but every other value in these
# queries is still passed as a bound parameter, never string-interpolated.
# This regex is defense-in-depth: it fails loudly if db_schema is ever
# misconfigured to something that isn't a plain SQL identifier, instead of
# silently building an invalid/unsafe query.
_SCHEMA_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validated_schema(schema: str) -> str:
    if not _SCHEMA_IDENTIFIER_RE.match(schema):
        raise ValueError(f"Unsafe db_schema configured: {schema!r}")
    return schema


class AuthRepository:
    """Encapsulates all database operations for login provisioning and identity resolution."""

    def __init__(self, db: Session):
        self.db = db
        self._schema = _validated_schema(settings.db_schema)

    def get_existing_user_for_keycloak_login(
        self, tenant_id: str, provider_subject_id: str
    ) -> Optional[dict[str, Any]]:
        """Return the existing sys_user/sys_userinfo identity for a Keycloak subject in a tenant."""
        row = self.db.execute(
            text(
                f"""
                SELECT u.user_id, u.tenant_id
                FROM {self._schema}.sys_userinfo ui
                JOIN {self._schema}.sys_user u ON ui.user_id = u.user_id
                WHERE ui.tenant_id = :tenant_id
                  AND ui.auth_provider = 'KEYCLOAK'
                  AND ui.provider_subject_id = :provider_subject_id
                """
            ),
            {"tenant_id": tenant_id, "provider_subject_id": provider_subject_id},
        ).mappings().first()
        return dict(row) if row is not None else None

    def refresh_login_metadata(self, user_id: str, tenant_id: str, provider_subject_id: str) -> None:
        """Update last_login_at on both sys_user and the matching sys_userinfo row."""
        self.db.execute(
            text(f"UPDATE {self._schema}.sys_user SET last_login_at = now() WHERE user_id = :uid"),
            {"uid": user_id},
        )
        self.db.execute(
            text(
                f"""
                UPDATE {self._schema}.sys_userinfo
                SET last_login_at = now()
                WHERE tenant_id = :tenant_id
                  AND auth_provider = 'KEYCLOAK'
                  AND provider_subject_id = :provider_subject_id
                """
            ),
            {"tenant_id": tenant_id, "provider_subject_id": provider_subject_id},
        )

    def provision_new_user_for_keycloak_login(
        self, tenant_id: str, payload: dict[str, Any], provider_subject_id: str
    ) -> Optional[dict[str, str]]:
        """Create a new sys_user + sys_userinfo pair when the tenant-scoped Keycloak identity is new."""
        username = payload.get("preferred_username") or payload.get("email") or provider_subject_id
        email = payload.get("email")
        full_name = payload.get("name")

        inserted_user = self.db.execute(
            text(
                f"""
                INSERT INTO {self._schema}.sys_user
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
            return None

        user_id = inserted_user["user_id"]
        self.db.execute(
            text(
                f"""
                INSERT INTO {self._schema}.sys_userinfo
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
        return {"user_id": str(user_id), "tenant_id": str(inserted_user["tenant_id"])}

    def get_or_create_keycloak_user(
        self, tenant_id: str, payload: dict[str, Any], provider_subject_id: str
    ) -> Optional[dict[str, str]]:
        """Single entry point for the Keycloak login flow: refreshes an
        existing identity's last_login_at, or provisions a new sys_user +
        sys_userinfo pair. Kept as one cohesive repository call so
        core.auth only owns session/transaction lifecycle (commit/rollback),
        not the lookup-vs-provision branching."""
        row = self.get_existing_user_for_keycloak_login(tenant_id, provider_subject_id)
        if row is not None:
            self.refresh_login_metadata(str(row["user_id"]), tenant_id, provider_subject_id)
            return {"user_id": str(row["user_id"]), "tenant_id": str(row["tenant_id"])}

        return self.provision_new_user_for_keycloak_login(tenant_id, payload, provider_subject_id)

