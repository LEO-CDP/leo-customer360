"""Password hashing + local dev JWT helpers.

Password hashing is stdlib-only (no new dependency): PBKDF2-HMAC-SHA256 via
``hashlib.pbkdf2_hmac`` with a random per-user salt, following the same
"$"-delimited encoding Django/Passlib use for their PBKDF2 hashers so the
format is self-describing and iteration count can be bumped later without
invalidating already-stored hashes.

The JWT helpers issue/verify the local dev-mode access token used by
POST /auth/login when SSO_LOGIN=false (see core/routers/auth_api.py) --
same ``Authorization: Bearer <token>`` contract as a real Keycloak token,
just HS256-signed with DEV_JWT_SECRET instead of Keycloak's keys. Never used
when SSO_LOGIN=true (Keycloak tokens are verified via introspection instead,
see core/auth.py::_introspect_with_keycloak).
"""

import hashlib
import hmac
import secrets
import time
from typing import Any, Optional

import jwt

from core.config import settings

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 260_000
_SALT_BYTES = 16

DEV_JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """Hash a plaintext password into ``algorithm$iterations$salt_hex$hash_hex``."""
    salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a hash produced by ``hash_password``."""
    try:
        algorithm, iterations_str, salt, hash_hex = password_hash.split("$", 3)
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations_str))
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def create_dev_access_token(*, tenant_id: str, user_id: Optional[str], username: str, roles: list[str]) -> tuple[str, int]:
    """Issues a locally-signed dev JWT carrying the same ``tenant_id``/``user_id``
    claims a real Keycloak token would (custom protocol mapper claims) --
    ``core.auth._resolve_tenant_and_user`` reads them identically either way.

    Returns ``(token, expires_in_seconds)``.
    """
    expires_in = settings.dev_jwt_expires_minutes * 60
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "customer360-api-dev",
        "sub": username,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "roles": roles,
        "preferred_username": username,
        "iat": now,
        "exp": now + expires_in,
    }
    token = jwt.encode(claims, settings.dev_jwt_secret, algorithm=DEV_JWT_ALGORITHM)
    return token, expires_in


def decode_dev_access_token(token: str) -> Optional[dict[str, Any]]:
    """Verifies a dev JWT signature/expiry and returns its claims, or ``None``
    if invalid/expired/not one of ours."""
    try:
        return jwt.decode(token, settings.dev_jwt_secret, algorithms=[DEV_JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
