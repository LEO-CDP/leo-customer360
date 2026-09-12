"""Admin routes for campaign activation + email dispatch config.

- POST /admin/campaigns/{id}/activate  -- gate on approval, submit the
  campaign_activation Dagster job (which snapshots the segment and hands off to
  the email send).
- GET  /admin/campaigns/{id}/dispatch-logs  -- per-recipient send ledger.
- GET/PUT /admin/email-provider-config  -- the tenant's dynamic email/SMTP
  config the email_engine resolves at send time.

All routes are tenant-scoped (RLS) and tenant-admin gated.
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import require_tenant, require_tenant_admin
from core.crud.email_provider import get_active_config, upsert_config
from core.database import get_db
from core.models.crm import Campaign, CampaignDispatchLog, EmailProviderConfig
from core.schemas.crm import (
    CampaignActivationResponse,
    CampaignDispatchLogRead,
    EmailProviderConfigRead,
    EmailProviderConfigUpsert,
)
from core.utils.dagster_client import DagsterJobTriggerError, dagster_client

logger = logging.getLogger(__name__)

campaign_activation_router = APIRouter(prefix="/admin", tags=["Campaign - Activation & Email"])


def _to_provider_read(config: EmailProviderConfig) -> EmailProviderConfigRead:
    read = EmailProviderConfigRead.model_validate(config)
    read.smtp_password_set = bool(config.smtp_password)
    return read


@campaign_activation_router.post("/campaigns/{campaign_id}/activate", response_model=CampaignActivationResponse)
def activate_campaign(campaign_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    """Validate the human-approval gate, then submit the activation run."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "campaign activation")

    campaign = db.get(Campaign, campaign_id)
    if campaign is None or str(campaign.tenant_id) != tenant_id:
        raise HTTPException(status_code=404, detail=f"Campaign '{campaign_id}' not found")

    if (campaign.approval_status or "").strip() != "Approved":
        raise HTTPException(status_code=409, detail=f"Campaign is not Approved (approval_status={campaign.approval_status!r})",)

    if not campaign.template_id or not campaign.segment_id:
        raise HTTPException(status_code=409, detail="Campaign needs both a template_id and a segment_id.")

    try:
        run_id = dagster_client.campaign_activation.activate(str(campaign_id), tenant_id)
    except DagsterJobTriggerError as exc:
        raise HTTPException(status_code=503, detail=f"Could not submit campaign activation: {exc}") from exc

    return CampaignActivationResponse(
        campaign_id=campaign_id, run_id=run_id, message=f"campaign_activation_job submitted (run_id={run_id})"
    )


@campaign_activation_router.get(
    "/campaigns/{campaign_id}/dispatch-logs", response_model=list[CampaignDispatchLogRead]
)
def list_dispatch_logs(
    campaign_id: uuid.UUID,
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status."),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """Per-recipient dispatch ledger for a campaign (audit evidence)."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "campaign activation")

    stmt = select(CampaignDispatchLog).where(
        CampaignDispatchLog.campaign_id == campaign_id,
        CampaignDispatchLog.tenant_id == uuid.UUID(tenant_id),
    )
    if status:
        stmt = stmt.where(CampaignDispatchLog.status == status)
    return db.execute(stmt.order_by(CampaignDispatchLog.updated_at.desc()).limit(limit)).scalars().all()


@campaign_activation_router.get("/email-provider-config", response_model=Optional[EmailProviderConfigRead])
def get_email_provider_config(request: Request, db: Session = Depends(get_db)):
    """The tenant's active email dispatch config (secret password omitted)."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "email config")
    config = get_active_config(db, uuid.UUID(tenant_id))
    return _to_provider_read(config) if config is not None else None


@campaign_activation_router.put("/email-provider-config", response_model=EmailProviderConfigRead)
def put_email_provider_config(payload: EmailProviderConfigUpsert, request: Request, db: Session = Depends(get_db)):
    """Create/update the tenant's email dispatch config; invalidates its cache."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "email config")
    return _to_provider_read(upsert_config(db, uuid.UUID(tenant_id), payload))


all_campaign_activation_routers = [campaign_activation_router]
