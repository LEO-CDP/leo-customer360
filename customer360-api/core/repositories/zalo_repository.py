"""Zalo connector implementation backed by ``crm_connector_config``."""

from datetime import datetime, timedelta, timezone
import uuid
from typing import Any

from core.repositories.connector_repository import BaseConnectorRepository
from leo_customer360_dao.crud import zalo_oa, zalo_templates
from leo_customer360_dao.models.crm import ConnectorConfig
from leo_customer360_dao.schemas.crm import ZaloConnectorConfigRead, ZaloOaConfigRead


class ZaloConnector(BaseConnectorRepository):
    """Implement Zalo-specific settings, OAuth, and ZNS template operations."""

    connector_type = "CHAT"
    provider = "ZALO"
    connector_name = "zalo"
    default_config = zalo_oa.ZALO_DEFAULT_CONFIG

    def connector_read(self, row: ConnectorConfig) -> ZaloConnectorConfigRead:
        """Map stored connector credentials and settings to the public schema."""
        values = self.get_values(row, include_credentials=True)
        credentials = row.credentials or {}
        return ZaloConnectorConfigRead(
            connector_id=row.connector_id, tenant_id=row.tenant_id, app_id=credentials.get("app_id"),
            app_secret_set=bool(credentials.get("app_secret")), webhook_signing_secret_set=bool(credentials.get("webhook_signing_secret")),
            access_token_set=bool(credentials.get("access_token")), oa_api_base_url=values["oa_api_base_url"],
            oauth_authorize_url=values["oauth_authorize_url"], oa_token_url=values["oa_token_url"], oauth_redirect_uri=values["oauth_redirect_uri"],
            token_refresh_cron=values["token_refresh_cron"], dispatch_adapter=values["dispatch_adapter"], zns_api_base_url=values["zns_api_base_url"],
            batch_size=int(values["batch_size"]), optout_projection_cron=values["optout_projection_cron"],
            optout_lookback_hours=int(values["optout_lookback_hours"]), is_active=row.is_active,
        )

    def upsert_connector_config(self, tenant_id: uuid.UUID, payload: dict[str, object]) -> ConnectorConfig:
        """Persist Zalo settings and credential fields in the shared connector row."""
        config_values = {key: payload[key] for key in self.default_config if key in payload}
        credential_values = {
            key: payload[key]
            for key in ("app_id", "app_secret", "webhook_signing_secret")
            if key in payload
        }
        return self.upsert_config(
            tenant_id,
            config_values=config_values,
            credential_values=credential_values,
            is_active=bool(payload.get("is_active", True)),
        )

    def get_oa_config(self, tenant_id: uuid.UUID) -> ConnectorConfig | None:
        """Load the tenant's Zalo OA connector configuration."""
        return self.get_config(tenant_id)

    def connector_values(self, row: ConnectorConfig) -> dict[str, Any]:
        """Return normalized connector settings for template synchronization."""
        return self.get_values(row, include_credentials=True)

    def build_authorize_url(self, tenant_id: uuid.UUID) -> str:
        """Build the signed tenant-bound Zalo OAuth URL."""
        return zalo_oa.build_authorize_url(self._require_session(), tenant_id)

    def verify_state(self, state: str) -> str:
        """Validate a signed OAuth state and return its tenant identifier."""
        return zalo_oa.verify_state(state)

    def exchange_code(self, oa_code: str, row: ConnectorConfig) -> dict[str, Any]:
        """Exchange a Zalo authorization code for access and refresh tokens."""
        return zalo_oa.exchange_oa_code(oa_code, row)

    def save_tokens(
        self,
        tenant_id: uuid.UUID,
        oa_id: str,
        access_token: str,
        refresh_token: str,
        expires_in: int,
    ) -> ConnectorConfig:
        """Persist Zalo OAuth tokens and their calculated expiration timestamp."""
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        return self.update_credentials(
            tenant_id,
            {
                "oa_id": oa_id,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_expires_at": expires_at.isoformat(),
            },
        )

    def sync_templates(self, tenant_id: uuid.UUID, oa_id, access_token: str, api_base_url: str) -> dict:
        """Fetch and persist the connected OA's approved ZNS templates."""
        return zalo_templates.sync_templates(self._require_session(), tenant_id, oa_id, access_token, api_base_url)

    def list_templates(self, tenant_id: uuid.UUID):
        """List synchronized ZNS templates for a tenant."""
        return zalo_templates.list_templates(self._require_session(), tenant_id)

    def get_template(self, tenant_id: uuid.UUID, template_id: uuid.UUID):
        """Load one synchronized ZNS template for a tenant."""
        return zalo_templates.get_template(self._require_session(), tenant_id, template_id)
