"""Authentication for tenant-scoped promotions management endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import promotions_settings
from model.tenant import Tenant

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


def require_management_tenant(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> int:
    """Authenticate a configured token and return its active promotions tenant ID."""
    try:
        tokens = json.loads(promotions_settings.management_tokens.get_secret_value())
        if (
            not isinstance(tokens, dict)
            or not tokens
            or any(
                not isinstance(key, str)
                or not key.strip()
                or key != key.strip()
                or not isinstance(token, str)
                or len(token) < 32
                or not token.isascii()
                or any(char.isspace() for char in token)
                for key, token in tokens.items()
            )
            or len(set(tokens.values())) != len(tokens)
        ):
            raise ValueError("invalid management token configuration")
    except (ValueError, TypeError):
        logger.error("Promotions management token configuration is missing or invalid")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Management authentication is not configured",
        ) from None

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    candidate = hashlib.sha256(credentials.credentials.encode("utf-8")).digest()
    tenant_key = None
    for key, token in tokens.items():
        if hmac.compare_digest(candidate, hashlib.sha256(token.encode("ascii")).digest()):
            tenant_key = key
    if tenant_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    with Session(request.app.state.promotions_engine) as session:
        tenant_id = session.scalar(
            select(Tenant.tenant_id).where(
                Tenant.tenant_key == tenant_key,
                Tenant.status == "active",
            )
        )
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Management tenant is unavailable",
        )
    return tenant_id
