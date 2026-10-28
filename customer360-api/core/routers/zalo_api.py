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
from core.database import get_db
from core.repositories.zalo_repository import ZaloConnector
from leo_customer360_dao.schemas.crm import (
    MessageTemplateRead,
    ZaloConnectorConfigRead,
    ZaloConnectorConfigUpsert,
    ZaloOaConfigRead,
    ZaloOauthUrlResponse,
)

logger = logging.getLogger(__name__)

zalo_router = APIRouter(prefix="/admin/zalo", tags=["Zalo OA"])


@zalo_router.put("/connector-config", response_model=ZaloConnectorConfigRead)
def put_zalo_connector_config(
    payload: ZaloConnectorConfigUpsert,
    request: Request,
    db: Session = Depends(get_db),
) -> ZaloConnectorConfigRead:
    """Create or update the caller tenant's database-backed Zalo settings."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "zalo connector config")
    repo = ZaloConnector(db)
    return repo.connector_read(repo.upsert_connector_config(uuid.UUID(tenant_id), payload.model_dump(exclude_unset=True)))


@zalo_router.get("/oa-config", response_model=ZaloOaConfigRead)
def get_zalo_oa_config(request: Request, db: Session = Depends(get_db)) -> ZaloOaConfigRead:
    """Connection status of the caller tenant's Zalo OA (no tokens returned)."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "zalo oa config")

    repo = ZaloConnector(db)
    row = repo.get_oa_config(uuid.UUID(tenant_id))
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
    return ZaloOauthUrlResponse(authorize_url=ZaloConnector(db).build_authorize_url(uuid.UUID(tenant_id)))


@zalo_router.post("/templates/sync")
def sync_zns_templates(request: Request, db: Session = Depends(get_db)) -> dict:
    """Pull the connected OA's approved ZNS templates from Zalo and upsert them
    into ``crm_message_templates`` (channel='zalo_zns'). Requires OA-admin."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "zalo template sync")

    repo = ZaloConnector(db)
    cfg = repo.get_oa_config(uuid.UUID(tenant_id))
    tokens = (cfg.credentials or {}) if cfg is not None else {}
    if not tokens.get("access_token"):
        raise HTTPException(status_code=400, detail="Zalo OA is not connected for this tenant")
    assert cfg is not None
    values = repo.connector_values(cfg)
    return repo.sync_templates(uuid.UUID(tenant_id), tokens.get("oa_id"), tokens["access_token"], values["oa_api_base_url"])


@zalo_router.get("/templates", response_model=list[MessageTemplateRead])
def list_zns_templates(request: Request, db: Session = Depends(get_db)):
    """The tenant's synced ZNS templates (name, status, quality, id, params)."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "zalo templates")
    return ZaloConnector(db).list_templates(uuid.UUID(tenant_id))


@zalo_router.get("/templates/{template_id}", response_model=MessageTemplateRead)
def get_zns_template(template_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    """One ZNS template's full content (params/preview) for the "view content" UI."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "zalo template")
    row = ZaloConnector(db).get_template(uuid.UUID(tenant_id), template_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"ZNS template '{template_id}' not found")
    return row


all_zalo_routers = [zalo_router]
