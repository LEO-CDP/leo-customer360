"""Zalo OA connector configuration and OAuth token persistence.

The tenant's ``CHAT/ZALO`` row in ``crm_connector_config`` is the single source
of truth. Non-secret settings live in ``config`` and app credentials, webhook
secrets, and rotating OAuth tokens live in ``credentials``.

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
from sqlalchemy import select
from sqlalchemy.orm import Session

from leo_customer360_dao.config import settings
from leo_customer360_dao.models.crm import ConnectorConfig

logger = logging.getLogger(__name__)


class ZaloOAError(Exception):
    """Domain error from the Zalo OA flow. Carries a suggested HTTP ``status`` as a
    plain int hint so the API layer can map it without the DAO importing a web
    framework (keeps this package framework-neutral)."""

    def __init__(self, message: str, *, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


ZALO_CONNECTOR_TYPE = "CHAT"
ZALO_PROVIDER = "ZALO"
ZALO_CONNECTOR_NAME = "zalo"
ZALO_DEFAULT_CONFIG = {
    "oa_api_base_url": "https://openapi.zalo.me",
    "oauth_authorize_url": "https://oauth.zaloapp.com/v4/oa/permission",
    "oa_token_url": "https://oauth.zaloapp.com/v4/oa/access_token",
    "oauth_redirect_uri": "",
    "token_refresh_cron": "*/30 * * * *",
    "dispatch_adapter": "mock",
    "zns_api_base_url": "https://business.openapi.zalo.me",
    "batch_size": 500,
    "optout_projection_cron": "*/15 * * * *",
    "optout_lookback_hours": 6,
}
_STATE_ALG = "HS256"
_STATE_TYP = "zalo_oauth_state"


def get_oa_config(db: Session, tenant_id: uuid.UUID) -> Optional[ConnectorConfig]:
    """Return the tenant's active Zalo connector row, or ``None``."""
    return db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.tenant_id == tenant_id,
            ConnectorConfig.connector_type == ZALO_CONNECTOR_TYPE,
            ConnectorConfig.provider == ZALO_PROVIDER,
            ConnectorConfig.status == "ACTIVE",
            ConnectorConfig.is_active.is_(True),
        )
        .order_by(ConnectorConfig.is_default.desc(), ConnectorConfig.updated_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def connector_values(row: ConnectorConfig) -> dict[str, Any]:
    """Merge persisted connector settings with defaults without exposing secrets."""
    values = dict(ZALO_DEFAULT_CONFIG)
    values.update(row.config or {})
    values.update(row.credentials or {})
    return values


def get_or_create_oa_config(db: Session, tenant_id: uuid.UUID) -> ConnectorConfig:
    """Return the tenant connector, creating a safe mock configuration if absent."""
    row = db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.tenant_id == tenant_id,
            ConnectorConfig.name == ZALO_CONNECTOR_NAME,
            ConnectorConfig.connector_type == ZALO_CONNECTOR_TYPE,
            ConnectorConfig.provider == ZALO_PROVIDER,
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = ConnectorConfig(
        tenant_id=tenant_id,
        name=ZALO_CONNECTOR_NAME,
        connector_type=ZALO_CONNECTOR_TYPE,
        provider=ZALO_PROVIDER,
        direction="BIDIRECTIONAL",
        status="ACTIVE",
        is_default=True,
        is_active=True,
        config=dict(ZALO_DEFAULT_CONFIG),
        credentials={},
    )
    db.add(row)
    db.flush()
    return row


def upsert_connector_config(
    db: Session,
    tenant_id: uuid.UUID,
    values: dict[str, Any],
) -> ConnectorConfig:
    """Create or update the tenant's Zalo connector configuration."""
    row = get_or_create_oa_config(db, tenant_id)
    config = dict(ZALO_DEFAULT_CONFIG)
    config.update(row.config or {})
    credentials = dict(row.credentials or {})
    for key in ZALO_DEFAULT_CONFIG:
        if key in values and values[key] is not None:
            config[key] = values[key]
    for key in ("app_id", "app_secret", "webhook_signing_secret"):
        if key in values and values[key] is not None:
            credentials[key] = values[key]
    row.config = config
    row.credentials = credentials
    row.status = "ACTIVE" if values.get("is_active", True) else "INACTIVE"
    row.is_active = values.get("is_active", True)
    row.is_default = row.is_active
    db.commit()
    db.refresh(row)
    return row


def upsert_oa_tokens(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    oa_id: str,
    access_token: str,
    refresh_token: str,
    expires_in: int,
) -> ConnectorConfig:
    """Persist freshly issued OAuth tokens in the tenant's connector credentials."""
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    row = get_or_create_oa_config(db, tenant_id)
    credentials = dict(row.credentials or {})
    credentials.update({
        "oa_id": oa_id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_expires_at": expires_at.isoformat(),
    })
    row.credentials = credentials
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
        raise ZaloOAError("Invalid or expired OAuth state", status=400) from exc
    if claims.get("typ") != _STATE_TYP or not claims.get("t"):
        raise ZaloOAError("Invalid OAuth state", status=400)
    return str(claims["t"])


def build_authorize_url(db: Session, tenant_id: uuid.UUID) -> str:
    """The Zalo OA consent URL the admin visits to authorize the OA. ⚠️ verify."""
    row = get_oa_config(db, tenant_id)
    if row is None:
        raise ZaloOAError("Zalo connector configuration is not active", status=503)
    values = connector_values(row)
    if not values.get("app_id") or not values.get("oauth_redirect_uri"):
        raise ZaloOAError("Zalo app_id and oauth_redirect_uri must be configured", status=503)
    params = {
        "app_id": values["app_id"],
        "redirect_uri": values["oauth_redirect_uri"],
        "state": sign_state(tenant_id),
    }
    return f"{values['oauth_authorize_url']}?{urllib.parse.urlencode(params)}"


def exchange_oa_code(oa_code: str, row: ConnectorConfig) -> dict[str, Any]:
    """Exchange the OA authorization code for access+refresh tokens.

    ⚠️ Endpoint/params/headers per the current Zalo OA OAuth v4 docs — confirm
    the ``secret_key`` header + ``grant_type`` before production.
    """
    values = connector_values(row)
    body = urllib.parse.urlencode(
        {
            "code": oa_code,
            "app_id": values["app_id"],
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        values["oa_token_url"],
        data=body,
        headers={
            "secret_key": values["app_secret"],
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
        raise ZaloOAError("Zalo OA token exchange failed", status=502) from exc
    except Exception as exc:
        logger.warning("Zalo OA token endpoint unreachable", exc_info=True)
        raise ZaloOAError("Zalo OA token endpoint unreachable", status=502) from exc

    if not data.get("access_token"):
        # Zalo returns {"error":...,"message":...} on failure.
        raise ZaloOAError(f"Zalo OA token exchange returned no access_token: {data}", status=502)
    return data
