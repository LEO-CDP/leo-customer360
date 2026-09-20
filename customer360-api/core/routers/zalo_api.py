"""Admin router for the Zalo OA (ZNS) outbound channel.

OA credentials/tokens are stored per-tenant in ``crm_connector_config`` as a
``CHAT/ZALO`` connector (see ``core.crud.zalo_oa``). These endpoints
report connection status and build the OAuth consent URL; the actual token
exchange happens in the public ``GET /api/v1/auth/zalo-redirect`` callback.

Every route is tenant-scoped and gated behind a tenant-admin role when SSO is on.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from core.auth import require_tenant, require_tenant_admin
from leo_customer360_dao.crud import zalo_oa, zalo_templates
from core.database import get_db
from leo_customer360_dao.schemas.crm import (
    MessageTemplateRead,
    ZaloConnectorConfigRead,
    ZaloConnectorConfigUpsert,
    ZaloOaConfigRead,
    ZaloOauthUrlResponse,
)

logger = logging.getLogger(__name__)

zalo_router = APIRouter(prefix="/admin/zalo", tags=["Zalo OA"])


def _connector_read(row) -> ZaloConnectorConfigRead:
    values = zalo_oa.connector_values(row)
    credentials = row.credentials or {}
    return ZaloConnectorConfigRead(
        connector_id=row.connector_id,
        tenant_id=row.tenant_id,
        app_id=credentials.get("app_id"),
        app_secret_set=bool(credentials.get("app_secret")),
        webhook_signing_secret_set=bool(credentials.get("webhook_signing_secret")),
        access_token_set=bool(credentials.get("access_token")),
        oa_api_base_url=values["oa_api_base_url"],
        oauth_authorize_url=values["oauth_authorize_url"],
        oa_token_url=values["oa_token_url"],
        oauth_redirect_uri=values["oauth_redirect_uri"],
        token_refresh_cron=values["token_refresh_cron"],
        dispatch_adapter=values["dispatch_adapter"],
        zns_api_base_url=values["zns_api_base_url"],
        batch_size=int(values["batch_size"]),
        optout_projection_cron=values["optout_projection_cron"],
        optout_lookback_hours=int(values["optout_lookback_hours"]),
        is_active=row.is_active,
    )


@zalo_router.put("/connector-config", response_model=ZaloConnectorConfigRead)
def put_zalo_connector_config(
    payload: ZaloConnectorConfigUpsert,
    request: Request,
    db: Session = Depends(get_db),
) -> ZaloConnectorConfigRead:
    """Create or update the caller tenant's database-backed Zalo settings."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "zalo connector config")
    row = zalo_oa.upsert_connector_config(
        db, uuid.UUID(tenant_id), payload.model_dump(exclude_unset=True)
    )
    return _connector_read(row)


@zalo_router.get("/oa-config", response_model=ZaloOaConfigRead)
def get_zalo_oa_config(request: Request, db: Session = Depends(get_db)) -> ZaloOaConfigRead:
    """Connection status of the caller tenant's Zalo OA (no tokens returned)."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "zalo oa config")

    row = zalo_oa.get_oa_config(db, uuid.UUID(tenant_id))
    if row is None:
        return ZaloOaConfigRead(connected=False)
    tokens = row.credentials or {}
    return ZaloOaConfigRead(
        connected=bool(tokens.get("access_token")),
        oa_id=tokens.get("oa_id"),
        app_id=tokens.get("app_id"),
        token_expires_at=tokens.get("token_expires_at"),
        has_refresh_token=bool(tokens.get("refresh_token")),
    )


@zalo_router.get("/oauth-url", response_model=ZaloOauthUrlResponse)
def get_zalo_oauth_url(request: Request, db: Session = Depends(get_db)) -> ZaloOauthUrlResponse:
    """Build the Zalo OA consent URL (carries a signed, tenant-bound state)."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "zalo oauth url")
    return ZaloOauthUrlResponse(
        authorize_url=zalo_oa.build_authorize_url(db, uuid.UUID(tenant_id))
    )


@zalo_router.post("/templates/sync")
def sync_zns_templates(request: Request, db: Session = Depends(get_db)) -> dict:
    """Pull the connected OA's approved ZNS templates from Zalo and upsert them
    into ``crm_message_templates`` (channel='zalo_zns'). Requires OA-admin."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "zalo template sync")

    cfg = zalo_oa.get_oa_config(db, uuid.UUID(tenant_id))
    tokens = (cfg.credentials or {}) if cfg is not None else {}
    if not tokens.get("access_token"):
        raise HTTPException(status_code=400, detail="Zalo OA is not connected for this tenant")
    values = zalo_oa.connector_values(cfg)
    return zalo_templates.sync_templates(
        db,
        uuid.UUID(tenant_id),
        tokens.get("oa_id"),
        tokens["access_token"],
        values["oa_api_base_url"],
    )


@zalo_router.get("/templates", response_model=list[MessageTemplateRead])
def list_zns_templates(request: Request, db: Session = Depends(get_db)):
    """The tenant's synced ZNS templates (name, status, quality, id, params)."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "zalo templates")
    return zalo_templates.list_templates(db, uuid.UUID(tenant_id))


@zalo_router.get("/templates/{template_id}", response_model=MessageTemplateRead)
def get_zns_template(template_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    """One ZNS template's full content (params/preview) for the "view content" UI."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "zalo template")
    row = zalo_templates.get_template(db, uuid.UUID(tenant_id), template_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"ZNS template '{template_id}' not found")
    return row


all_zalo_routers = [zalo_router]
