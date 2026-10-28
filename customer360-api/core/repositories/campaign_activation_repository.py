"""Persistence and orchestration repository for campaign activation."""

import uuid
from typing import Optional

from sqlalchemy import select

from leo_customer360_dao.crud.email_provider import get_active_config as default_get_active_config, upsert_config as default_upsert_config
from leo_customer360_dao.models.crm import Campaign, CampaignDispatchLog, ConnectorConfig
from leo_customer360_dao.models.segmentation import CdpSegment
from leo_customer360_dao.schemas.crm import EmailProviderConfigRead
from leo_customer360_dao.workflow import FlowNotFound, WorkflowRepository


class CampaignActivationNotFound(LookupError):
    """The requested campaign is not owned by the active tenant."""


class CampaignActivationConflict(ValueError):
    """The campaign is not ready for activation."""


class CampaignActivationRepository:
    """Encapsulates campaign activation and tenant provider configuration operations."""

    def __init__(self, session, dagster_client, get_active_config=default_get_active_config, upsert_config=default_upsert_config):
        """Create a repository with injectable service functions for tests."""
        self.session = session
        self.dagster = dagster_client
        self.get_active_config_fn = get_active_config
        self.upsert_config_fn = upsert_config

    def get_campaign(self, campaign_id: uuid.UUID, tenant_id: str):
        """Load a campaign only when it belongs to the requested tenant."""
        campaign = self.session.get(Campaign, campaign_id)
        return campaign if campaign is not None and str(campaign.tenant_id) == tenant_id else None

    def activate(self, campaign_id: uuid.UUID, tenant_id: str, *, restart: bool = False):
        """Validate the pinned approval and audience before submitting the job."""
        campaign = self.get_campaign(campaign_id, tenant_id)
        if campaign is None:
            raise CampaignActivationNotFound(f"Campaign '{campaign_id}' not found")
        if campaign.approval_status != "Approved":
            raise CampaignActivationConflict(
                f"Campaign is not Approved (approval_status={campaign.approval_status!r})"
            )
        if campaign.status == "Running":
            raise CampaignActivationConflict(f"Campaign is already {campaign.status}")
        if campaign.status == "Completed" and not restart:
            raise CampaignActivationConflict("Completed campaigns require an explicit restart.")
        if restart and campaign.status != "Completed":
            raise CampaignActivationConflict("Only completed campaigns can be explicitly restarted.")
        if not campaign.segment_id or not campaign.workflow_id or campaign.workflow_version is None:
            raise CampaignActivationConflict(
                "Campaign needs a segment_id and a pinned workflow_id and workflow_version."
            )
        try:
            version = WorkflowRepository(self.session, tenant_id).version_info(
                campaign.workflow_id, campaign.workflow_version,
            )
        except FlowNotFound as exc:
            raise CampaignActivationConflict("Pinned workflow version was not found") from exc
        if version["approval_status"] != "Approved":
            raise CampaignActivationConflict("Pinned workflow version must be Approved")
        segment = self.session.get(CdpSegment, campaign.segment_id)
        if (
            segment is None or str(segment.tenant_id) != tenant_id
            or not segment.is_active or segment.status_code != 1 or not segment.segment_tag
        ):
            raise CampaignActivationConflict("Campaign audience must be active and computed")
        return self.dagster.campaign_activation.activate(
            str(campaign_id), tenant_id, restart=restart,
            expected_execution_id=(
                str(campaign.workflow_execution_id) if campaign.workflow_execution_id else None
            ),
        )

    def list_dispatch_logs(self, campaign_id: uuid.UUID, tenant_id: str, status: Optional[str], limit: int):
        """Query a tenant-scoped campaign dispatch ledger."""
        statement = select(CampaignDispatchLog).where(CampaignDispatchLog.campaign_id == campaign_id, CampaignDispatchLog.tenant_id == uuid.UUID(tenant_id))
        if status:
            statement = statement.where(CampaignDispatchLog.status == status)
        return self.session.execute(statement.order_by(CampaignDispatchLog.updated_at.desc()).limit(limit)).scalars().all()

    @staticmethod
    def provider_read(config: ConnectorConfig) -> EmailProviderConfigRead:
        """Map stored provider values while exposing only password presence."""
        values = config.config if hasattr(config, "config") else {}
        credentials = config.credentials if hasattr(config, "credentials") else {}
        config_id = getattr(config, "connector_id", None) or getattr(config, "config_id")
        read = EmailProviderConfigRead(config_id=config_id, tenant_id=config.tenant_id, name=config.name, provider=str(config.provider).lower(), smtp_host=values.get("smtp_host", getattr(config, "smtp_host", None)), smtp_port=values.get("smtp_port", getattr(config, "smtp_port", None)), smtp_username=values.get("smtp_username", getattr(config, "smtp_username", None)), credentials_ref=getattr(config, "credentials_ref", None), smtp_use_tls=values.get("smtp_use_tls", getattr(config, "smtp_use_tls", True)), from_address=values.get("from_address", getattr(config, "from_address", None)), from_name=values.get("from_name", getattr(config, "from_name", None)), is_active=config.is_active, metadata_=getattr(config, "metadata_", None), created_at=getattr(config, "created_at", None), updated_at=getattr(config, "updated_at", None))
        read.smtp_password_set = bool(credentials.get("password", getattr(config, "smtp_password", None)) or getattr(config, "credentials_ref", None))
        return read

    def get_provider_config(self, tenant_id: uuid.UUID):
        """Load the active provider configuration for a tenant."""
        return self.get_active_config_fn(self.session, tenant_id)

    def upsert_provider_config(self, tenant_id: uuid.UUID, payload):
        """Create or update the provider configuration for a tenant."""
        return self.upsert_config_fn(self.session, tenant_id, payload)