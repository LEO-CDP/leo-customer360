"""Repository for tenant-scoped data-source metadata."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

from leo_customer360_dao.crud.base import CRUDBase
from leo_customer360_dao.models.system import SysDataSource

from core.repositories.metadata_repository import (
	DEFAULT_TENANT_ID,
	MetadataNotFoundError,
	MetadataRepository,
	MetadataRepositoryError,
)

logger = logging.getLogger(__name__)


class DataSourceRepository(MetadataRepository):
	"""Encapsulate data-source persistence and QR-code business rules."""

	def __init__(self, session=None):
		"""Create a data-source repository for the supplied database session."""
		super().__init__(session)
		self._crud = CRUDBase(SysDataSource)

	def list_data_sources(
		self,
		tenant_id: uuid.UUID = DEFAULT_TENANT_ID,
		status: int | None = None,
		skip: int = 0,
		limit: int = 100,
	) -> list[SysDataSource]:
		"""Return paginated data sources for one tenant."""
		try:
			return self._crud.list(self._require_session(), tenant_id=tenant_id, status=status, skip=skip, limit=limit)
		except Exception as exc:  # noqa: BLE001
			logger.warning("Failed to load data-source metadata from PostgreSQL", exc_info=True)
			raise MetadataRepositoryError(f"Data-source metadata unavailable: {exc}") from exc

	def count_data_sources(self, tenant_id: uuid.UUID = DEFAULT_TENANT_ID, status: int | None = None) -> int:
		"""Count data sources matching the tenant and optional status."""
		try:
			filters: dict[str, Any] = {"tenant_id": tenant_id}
			if status is not None:
				filters["status"] = status
			return self._crud.count(self._require_session(), **filters)
		except Exception as exc:  # noqa: BLE001
			logger.warning("Failed to count data-source metadata in PostgreSQL", exc_info=True)
			raise MetadataRepositoryError(f"Data-source metadata unavailable: {exc}") from exc

	def get_data_source(self, data_source_id: uuid.UUID) -> SysDataSource:
		"""Return one data source or raise the repository not-found error."""
		try:
			obj = self._crud.get(self._require_session(), data_source_id)
		except Exception as exc:  # noqa: BLE001
			logger.warning("Failed to load data-source metadata from PostgreSQL", exc_info=True)
			raise MetadataRepositoryError(f"Data-source metadata unavailable: {exc}") from exc
		if obj is None:
			raise MetadataNotFoundError(f"SysDataSource '{data_source_id}' not found")
		return obj

	def create_data_source(self, payload: dict[str, Any]) -> SysDataSource:
		"""Create a data source and generate QR tracking data when needed."""
		data = dict(payload)
		if data.get("data_source_url") and not data.get("qr_code_data"):
			data["qr_code_data"] = self._generate_qr_code_data(data["data_source_url"], data.get("slug", "datasource"))
		return self._crud.create(self._require_session(), data)

	def update_data_source(self, data_source_id: uuid.UUID, payload: dict[str, Any]) -> SysDataSource:
		"""Update a data source and refresh QR tracking data when its URL changes."""
		obj = self.get_data_source(data_source_id)
		data = dict(payload)
		if "data_source_url" in data and data["data_source_url"] and "qr_code_data" not in data:
			data["qr_code_data"] = self._generate_qr_code_data(data["data_source_url"], data.get("slug") or obj.slug or "datasource")
		try:
			return self._crud.update(self._require_session(), obj, data)
		except Exception as exc:  # noqa: BLE001
			logger.warning("Failed to update data-source metadata in PostgreSQL", exc_info=True)
			raise MetadataRepositoryError(f"Data-source metadata unavailable: {exc}") from exc

	def delete_data_source(self, data_source_id: uuid.UUID) -> None:
		"""Delete a data source after confirming that it exists."""
		obj = self.get_data_source(data_source_id)
		try:
			self._crud.delete(self._require_session(), obj)
		except Exception as exc:  # noqa: BLE001
			logger.warning("Failed to delete data-source metadata from PostgreSQL", exc_info=True)
			raise MetadataRepositoryError(f"Data-source metadata unavailable: {exc}") from exc

	@staticmethod
	def _generate_qr_code_data(data_source_url: str, slug: str) -> dict[str, Any]:
		"""Build QR target and campaign metadata for a data-source URL."""
		tracking_url = f"{data_source_url}{'&' if '?' in data_source_url else '?'}utm_source={slug}&utm_medium=qr_code&utm_campaign=c360_datasource"
		return {
			"target_url": data_source_url,
			"tracking_url": tracking_url,
			"qr_code_url": f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={quote_plus(tracking_url)}",
			"generated_at": datetime.now(timezone.utc).isoformat(),
		}
