"""Reusable persistence primitives for tenant connector configurations."""

import uuid
from typing import Any, ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from leo_customer360_dao.models.crm import ConnectorConfig


class BaseConnectorRepository:
	"""Provide shared CRUD behavior for connector rows in one database table."""

	connector_type: ClassVar[str]
	provider: ClassVar[str]
	connector_name: ClassVar[str]
	default_config: ClassVar[dict[str, Any]] = {}

	def __init__(self, session: Session | None):
		"""Bind the connector repository to a SQLAlchemy session."""
		self.session = session

	def _require_session(self) -> Session:
		"""Return the database session required by persistence operations."""
		if self.session is None:
			raise RuntimeError("A database session is required for connector persistence")
		return self.session

	def get_config(self, tenant_id: uuid.UUID, active_only: bool = True) -> ConnectorConfig | None:
		"""Load this connector's tenant row, optionally requiring an active row."""
		conditions = [
			ConnectorConfig.tenant_id == tenant_id,
			ConnectorConfig.connector_type == self.connector_type,
			ConnectorConfig.provider == self.provider,
		]
		if active_only:
			conditions.extend(
				[
					ConnectorConfig.status == "ACTIVE",
					ConnectorConfig.is_active.is_(True),
				]
			)
		session = self._require_session()
		return session.execute(
			select(ConnectorConfig)
			.where(*conditions)
			.order_by(ConnectorConfig.is_default.desc(), ConnectorConfig.updated_at.desc())
			.limit(1)
		).scalar_one_or_none()

	def get_or_create_config(self, tenant_id: uuid.UUID) -> ConnectorConfig:
		"""Load this connector's row or create its safe default configuration."""
		session = self._require_session()
		row = session.execute(
			select(ConnectorConfig).where(
				ConnectorConfig.tenant_id == tenant_id,
				ConnectorConfig.name == self.connector_name,
				ConnectorConfig.connector_type == self.connector_type,
				ConnectorConfig.provider == self.provider,
			)
		).scalar_one_or_none()
		if row is not None:
			return row
		row = ConnectorConfig(
			tenant_id=tenant_id,
			name=self.connector_name,
			connector_type=self.connector_type,
			provider=self.provider,
			direction="BIDIRECTIONAL",
			status="ACTIVE",
			is_default=True,
			is_active=True,
			config=dict(self.default_config),
			credentials={},
		)
		session.add(row)
		session.flush()
		return row

	def get_values(self, row: ConnectorConfig, include_credentials: bool = False) -> dict[str, Any]:
		"""Return defaulted settings, optionally including secret credentials."""
		values = dict(self.default_config)
		values.update(row.config or {})
		if include_credentials:
			values.update(row.credentials or {})
		return values

	def upsert_config(
		self,
		tenant_id: uuid.UUID,
		config_values: dict[str, Any],
		credential_values: dict[str, Any] | None = None,
		is_active: bool = True,
	) -> ConnectorConfig:
		"""Persist non-secret settings and credentials without replacing old keys."""
		row = self.get_or_create_config(tenant_id)
		config = dict(self.default_config)
		config.update(row.config or {})
		config.update({key: value for key, value in config_values.items() if value is not None})
		credentials = dict(row.credentials or {})
		credentials.update({key: value for key, value in (credential_values or {}).items() if value is not None})
		row.config = config
		row.credentials = credentials
		row.status = "ACTIVE" if is_active else "INACTIVE"
		row.is_active = is_active
		row.is_default = is_active
		session = self._require_session()
		session.commit()
		session.refresh(row)
		return row

	def update_credentials(self, tenant_id: uuid.UUID, values: dict[str, Any]) -> ConnectorConfig:
		"""Merge rotating credentials into the tenant connector row."""
		row = self.get_or_create_config(tenant_id)
		credentials = dict(row.credentials or {})
		credentials.update(values)
		row.credentials = credentials
		session = self._require_session()
		session.commit()
		session.refresh(row)
		return row