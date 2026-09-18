"""Zalo OA credential store — reuses ``sys_data_source`` (slug='zalo-oa'), no new table.

customer360-api owns the write side; the notification_engine (dispatch) and the
ZNS template sync read the tenant's ``zalo-oa`` row at run time. The rotating
OAuth token lives in ``access_tokens`` JSONB; the app secret in ``security_code``.

Zalo Open API endpoints/params are marked ⚠️ — confirm against the current Zalo
OA OAuth v4 docs before production.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import settings
from core.models.system import SysDataSource

logger = logging.getLogger(__name__)

ZALO_OA_SLUG = "zalo-oa"
_STATE_ALG = "HS256"
_STATE_TYP = "zalo_oauth_state"


def get_oa_config(db: Session, tenant_id: uuid.UUID) -> Optional[SysDataSource]:
    """The tenant's Zalo OA ``sys_data_source`` row (at most one), or None."""
    return db.execute(
        select(SysDataSource).where(
            SysDataSource.tenant_id == tenant_id,
            SysDataSource.slug == ZALO_OA_SLUG,
        )
    ).scalar_one_or_none()


def upsert_oa_tokens(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    oa_id: str,
    access_token: str,
    refresh_token: str,
    expires_in: int,
) -> SysDataSource:
    """Create/update the tenant's ``zalo-oa`` row with a freshly issued token.

    Token fields are merged into ``access_tokens`` JSONB so a refresh never
    drops the other keys; ``token_expires_at`` is stored ISO-8601 (JSONB is
    untyped) and parsed by the refresh job / dispatch engine.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    token_fields = {
        "oa_id": oa_id,
        "app_id": settings.crm_zalo_oa_app_id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_expires_at": expires_at.isoformat(),
    }
    row = get_oa_config(db, tenant_id)
    if row is None:
        row = SysDataSource(
            tenant_id=tenant_id,
            name="Zalo OA",
            slug=ZALO_OA_SLUG,
            source_type=2,  # Data Connector API
            status=1,
            data_source_url=settings.crm_zalo_oa_api_base_url,
            security_code=settings.crm_zalo_oa_app_secret,
            access_tokens=token_fields,
        )
        db.add(row)
    else:
        merged = dict(row.access_tokens or {})
        merged.update(token_fields)
        row.access_tokens = merged
        row.security_code = settings.crm_zalo_oa_app_secret
        row.data_source_url = settings.crm_zalo_oa_api_base_url
    db.commit()
    db.refresh(row)
    return row


# --- signed OAuth state (CSRF + tenant binding) --------------------------
def sign_state(tenant_id: str, ttl_seconds: int = 600) -> str:
    """A short-lived JWT bound to the tenant, echoed back as the OAuth ``state``."""
    now = datetime.now(timezone.utc)
    claims = {
        "t": str(tenant_id),
        "typ": _STATE_TYP,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(claims, settings.dev_jwt_secret, algorithm=_STATE_ALG)


def verify_state(state: str) -> str:
    """Return the tenant_id carried by a valid, unexpired OAuth ``state``."""
    try:
        claims = jwt.decode(state, settings.dev_jwt_secret, algorithms=[_STATE_ALG])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state") from exc
    if claims.get("typ") != _STATE_TYP or not claims.get("t"):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    return str(claims["t"])


def build_authorize_url(tenant_id: str) -> str:
    """The Zalo OA consent URL the admin visits to authorize the OA. ⚠️ verify."""
    params = {
        "app_id": settings.crm_zalo_oa_app_id,
        "redirect_uri": settings.crm_zalo_oauth_redirect_uri,
        "state": sign_state(tenant_id),
    }
    return f"{settings.crm_zalo_oauth_authorize_url}?{urllib.parse.urlencode(params)}"


def exchange_oa_code(oa_code: str) -> dict[str, Any]:
    """Exchange the OA authorization code for access+refresh tokens.

    ⚠️ Endpoint/params/headers per the current Zalo OA OAuth v4 docs — confirm
    the ``secret_key`` header + ``grant_type`` before production.
    """
    body = urllib.parse.urlencode(
        {
            "code": oa_code,
            "app_id": settings.crm_zalo_oa_app_id,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        settings.crm_zalo_oa_token_url,
        data=body,
        headers={
            "secret_key": settings.crm_zalo_oa_app_secret,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        logger.warning("Zalo OA token exchange failed HTTP %s: %s", exc.code, detail)
        raise HTTPException(status_code=502, detail="Zalo OA token exchange failed") from exc
    except Exception as exc:
        logger.warning("Zalo OA token endpoint unreachable", exc_info=True)
        raise HTTPException(status_code=502, detail="Zalo OA token endpoint unreachable") from exc

    if not data.get("access_token"):
        # Zalo returns {"error":...,"message":...} on failure.
        raise HTTPException(status_code=502, detail=f"Zalo OA token exchange returned no access_token: {data}")
    return data
