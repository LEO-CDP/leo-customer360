"""Admin routes for campaign activation + email dispatch config.

- POST /admin/campaigns/{id}/activate  -- gate on approval, submit the
  campaign_activation Dagster job (which snapshots the segment and hands off
  the persisted workflow.
- GET  /admin/campaigns/{id}/dispatch-logs  -- per-recipient send ledger.
- GET/PUT /admin/email-provider-config  -- tenant workflow email connector
  configuration.

All routes are tenant-scoped (RLS) and tenant-admin gated.
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from core.auth import require_tenant, require_tenant_admin
from leo_customer360_dao.crud.email_provider import get_active_config, upsert_config
from core.database import get_db
from core.repositories.campaign_activation_repository import (
    CampaignActivationConflict,
    CampaignActivationNotFound,
    CampaignActivationRepository,
)
from leo_customer360_dao.schemas.crm import (
    CampaignActivationRequest,
    CampaignActivationResponse,
    CampaignDispatchLogRead,
    EmailProviderConfigRead,
    EmailProviderConfigUpsert,
)
from core.utils.dagster_client import DagsterJobTriggerError, dagster_client

logger = logging.getLogger(__name__)

campaign_activation_router = APIRouter(prefix="/admin", tags=["Campaign - Activation & Email"])


def _to_provider_read(config) -> EmailProviderConfigRead:
    """Map provider config without exposing stored SMTP credentials.

    The repository owns the canonical mapper; keeping this compatibility
    wrapper avoids duplicating secret-redaction logic in route handlers.
    """
    return CampaignActivationRepository(None, dagster_client).provider_read(config)


@campaign_activation_router.post("/campaigns/{campaign_id}/activate", response_model=CampaignActivationResponse)
def activate_campaign(
    campaign_id: uuid.UUID,
    request: Request,
    payload: CampaignActivationRequest = Body(default=CampaignActivationRequest()),
    db: Session = Depends(get_db),
):
    """Validate the human-approval gate, then submit the activation run."""
    # Activation is deliberately admin-only and separate from campaign
    # approval: approving a draft never sends messages by itself.
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "campaign activation")

    repo = CampaignActivationRepository(db, dagster_client)
    try:
        run_id = repo.activate(campaign_id, tenant_id, restart=payload.restart)
    except CampaignActivationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CampaignActivationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DagsterJobTriggerError as exc:
        logger.error(
            "Campaign activation submission failed: campaign_id=%s tenant_id=%s restart=%s dagster_error=%s",
            campaign_id,
            tenant_id,
            payload.restart,
            str(exc),
        )
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

    return CampaignActivationRepository(db, dagster_client).list_dispatch_logs(campaign_id, tenant_id, status, limit)


@campaign_activation_router.get("/email-provider-config", response_model=Optional[EmailProviderConfigRead])
def get_email_provider_config(request: Request, db: Session = Depends(get_db)):
    """The tenant's active email dispatch config (secret password omitted)."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "email config")
    repo = CampaignActivationRepository(db, dagster_client, get_active_config=get_active_config)
    config = repo.get_provider_config(uuid.UUID(tenant_id))
    return repo.provider_read(config) if config is not None else None


@campaign_activation_router.put("/email-provider-config", response_model=EmailProviderConfigRead)
def put_email_provider_config(payload: EmailProviderConfigUpsert, request: Request, db: Session = Depends(get_db)):
    """Create or update the tenant's email dispatch config."""
    tenant_id = require_tenant(request)
    require_tenant_admin(request, "email config")
    repo = CampaignActivationRepository(db, dagster_client, upsert_config=upsert_config)
    return repo.provider_read(repo.upsert_provider_config(uuid.UUID(tenant_id), payload))


all_campaign_activation_routers = [campaign_activation_router]
