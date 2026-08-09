"""Repository and service layer for system metadata endpoints.

Keeps FastAPI transport code out of core metadata/business logic so routers
stay focused on request parsing and HTTP response mapping.
"""

import logging
import socket
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote_plus

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from core.cache import get_redis_client
from core.config import settings
from core.crud.base import CRUDBase
from core.database import engine
from core.models.identity import CdpScoringModel
from core.models.system import SysDataSource, SysDomain, SysTenantDomain
from core.utils.dagster_client import DagsterClient

logger = logging.getLogger(__name__)

# Short timeouts keep the metadata endpoint responsive even when a service
# is down; a slow dependency should not make this endpoint hang.
CONNECTIVITY_TIMEOUT_SECONDS = 2

# The default tenant seeded by database-init/init-core-database.sql.
DEFAULT_TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class MetadataRepositoryError(Exception):
	"""Base error for metadata repository failures."""


class MetadataNotFoundError(MetadataRepositoryError):
	"""Raised when a requested metadata entity does not exist."""


class MetadataConflictError(MetadataRepositoryError):
	"""Raised when a metadata write violates a business invariant."""


class MetadataRepository:
	"""Encapsulates metadata queries and related business logic."""

	def __init__(self, session: Optional[Session] = None):
		self.session = session
		self._data_source_crud = CRUDBase(SysDataSource)
		self._scoring_model_crud = CRUDBase(CdpScoringModel)

	def _require_session(self) -> Session:
		if self.session is None:
			raise MetadataRepositoryError("Database session is required for this operation")
		return self.session

	def _check_postgres(self) -> dict[str, Any]:
		"""Checks that the pooled SQLAlchemy engine can reach PostgreSQL."""
		result = {
			"service": "postgres",
			"status": "unknown",
			"host": settings.db_host,
			"port": settings.db_port,
			"database": settings.db_name,
		}
		try:
			with engine.connect() as conn:
				conn.execute(text("SELECT 1"))
			result["status"] = "reachable"
		except Exception as exc:  # noqa: BLE001
			logger.warning("Postgres health check failed", exc_info=True)
			result["status"] = "unreachable"
			result["error"] = str(exc)
		return result

	def _check_redis(self) -> dict[str, Any]:
		"""Checks Redis cache connectivity, or reports it as disabled."""
		result = {
			"service": "redis",
			"status": "unknown",
			"host": settings.redis_host,
			"port": settings.redis_port,
			"db": settings.redis_db,
		}
		client = get_redis_client()
		if client is None:
			result["status"] = "disabled"
			result["note"] = "Response caching is disabled or Redis is not configured"
			return result

		try:
			client.ping()
			result["status"] = "reachable"
		except Exception as exc:  # noqa: BLE001
			logger.warning("Redis health check failed", exc_info=True)
			result["status"] = "unreachable"
			result["error"] = str(exc)
		return result

	def _check_dagster(self) -> dict[str, Any]:
		"""Checks whether the Dagster GraphQL webserver is accepting TCP connections."""
		result = {
			"service": "dagster",
			"status": "unknown",
			"host": settings.dagster_graphql_host,
			"port": settings.dagster_graphql_port,
		}
		try:
			sock = socket.create_connection(
				(settings.dagster_graphql_host, settings.dagster_graphql_port),
				timeout=CONNECTIVITY_TIMEOUT_SECONDS,
			)
			sock.close()
			result["status"] = "reachable"
		except Exception as exc:  # noqa: BLE001
			logger.warning("Dagster health check failed", exc_info=True)
			result["status"] = "unreachable"
			result["error"] = str(exc)
		return result

	def _service_status(self) -> dict[str, Any]:
		return {
			"postgres": self._check_postgres(),
			"redis": self._check_redis(),
			"dagster": self._check_dagster(),
		}

	def _generate_qr_code_data(self, data_source_url: str, slug: str) -> dict[str, Any]:
		tracking_url = (
			f"{data_source_url}?utm_source={slug}&utm_medium=qr_code&utm_campaign=c360_datasource"
			if "?" not in data_source_url
			else f"{data_source_url}&utm_source={slug}&utm_medium=qr_code&utm_campaign=c360_datasource"
		)
		return {
			"target_url": data_source_url,
			"tracking_url": tracking_url,
			"qr_code_url": f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={quote_plus(tracking_url)}",
			"generated_at": datetime.now(timezone.utc).isoformat(),
		}

	def get_system_metadata(self) -> dict[str, Any]:
		services = self._service_status()
		overall = "healthy" if all(
			service["status"] in ("reachable", "disabled") for service in services.values()
		) else "degraded"
		return {
			"service": "customer360-api",
			"api_version": settings.api_version,
			"environment": settings.environment,
			"sso_login": settings.sso_login,
			"overall_status": overall,
			"services": services,
		}

	def get_dagster_metadata(self) -> dict[str, Any]:
		connectivity = self._check_dagster()
		client = DagsterClient()
		services = []
		for attr_name in dir(client):
			if attr_name.startswith("_"):
				continue
			service = getattr(client, attr_name)
			if not hasattr(service, "job_name"):
				continue
			services.append(
				{
					"name": attr_name,
					"job_name": service.job_name,
					"location_name": service.location_name,
					"repository_name": service.repository_name,
				}
			)
		return {
			"service": "dagster",
			"status": connectivity["status"],
			"host": connectivity.get("host"),
			"port": connectivity.get("port"),
			"error": connectivity.get("error"),
			"configured_services": services,
		}

	def get_domains(self, tenant_id: uuid.UUID = DEFAULT_TENANT_ID) -> dict[str, str]:
		session = self._require_session()
		stmt = (
			select(SysDomain.domain_code, SysDomain.domain_name)
			.join(SysTenantDomain, SysTenantDomain.domain_id == SysDomain.domain_id)
			.where(
				SysTenantDomain.tenant_id == tenant_id,
				SysTenantDomain.is_active.is_(True),
				SysDomain.is_active.is_(True),
			)
			.order_by(SysDomain.display_order, SysDomain.domain_code)
		)
		try:
			rows = session.execute(stmt).all()
		except Exception as exc:  # noqa: BLE001
			logger.warning("Failed to load domain metadata from PostgreSQL", exc_info=True)
			raise MetadataRepositoryError(f"Domain metadata unavailable: {exc}") from exc
		return {domain_code: domain_name for domain_code, domain_name in rows}

	def list_data_sources(
		self,
		tenant_id: uuid.UUID = DEFAULT_TENANT_ID,
		status: int | None = None,
		skip: int = 0,
		limit: int = 100,
	) -> list[SysDataSource]:
		session = self._require_session()
		try:
			return self._data_source_crud.list(
				session,
				tenant_id=tenant_id,
				status=status,
				skip=skip,
				limit=limit,
			)
		except Exception as exc:  # noqa: BLE001
			logger.warning("Failed to load data-source metadata from PostgreSQL", exc_info=True)
			raise MetadataRepositoryError(f"Data-source metadata unavailable: {exc}") from exc

	def get_data_source(self, data_source_id: uuid.UUID) -> SysDataSource:
		session = self._require_session()
		obj = self._data_source_crud.get(session, data_source_id)
		if obj is None:
			raise MetadataNotFoundError(f"SysDataSource '{data_source_id}' not found")
		return obj

	def create_data_source(self, payload: dict[str, Any]) -> SysDataSource:
		session = self._require_session()
		data = dict(payload)
		if data.get("data_source_url") and not data.get("qr_code_data"):
			data["qr_code_data"] = self._generate_qr_code_data(
				data["data_source_url"],
				data.get("slug", "datasource"),
			)
		return self._data_source_crud.create(session, data)

	def update_data_source(self, data_source_id: uuid.UUID, payload: dict[str, Any]) -> SysDataSource:
		obj = self.get_data_source(data_source_id)
		data = dict(payload)
		if "data_source_url" in data and data["data_source_url"] and "qr_code_data" not in data:
			slug = data.get("slug") or obj.slug or "datasource"
			data["qr_code_data"] = self._generate_qr_code_data(data["data_source_url"], slug)
		return self._data_source_crud.update(self._require_session(), obj, data)

	def delete_data_source(self, data_source_id: uuid.UUID) -> None:
		obj = self.get_data_source(data_source_id)
		self._data_source_crud.delete(self._require_session(), obj)

	def list_scoring_models(
		self,
		status: str | None = None,
		model_type: str | None = None,
		skip: int = 0,
		limit: int = 100,
	) -> list[CdpScoringModel]:
		session = self._require_session()
		try:
			return self._scoring_model_crud.list(
				session,
				status=status,
				model_type=model_type,
				skip=skip,
				limit=limit,
				sort_by="updated_at DESC",
			)
		except Exception as exc:  # noqa: BLE001
			logger.warning("Failed to load scoring model metadata from PostgreSQL", exc_info=True)
			raise MetadataRepositoryError(f"Scoring model metadata unavailable: {exc}") from exc

	def get_scoring_model(self, scoring_model_name: str) -> CdpScoringModel:
		session = self._require_session()
		obj = self._scoring_model_crud.get(session, scoring_model_name)
		if obj is None:
			raise MetadataNotFoundError(f"CdpScoringModel '{scoring_model_name}' not found")
		return obj

	def create_scoring_model(self, payload: dict[str, Any]) -> CdpScoringModel:
		session = self._require_session()
		if self._scoring_model_crud.get(session, payload["scoring_model_name"]) is not None:
			raise MetadataConflictError(
				f"CdpScoringModel '{payload['scoring_model_name']}' already exists"
			)
		return self._scoring_model_crud.create(session, payload)

	def update_scoring_model(self, scoring_model_name: str, payload: dict[str, Any]) -> CdpScoringModel:
		obj = self.get_scoring_model(scoring_model_name)
		return self._scoring_model_crud.update(self._require_session(), obj, payload)

	def delete_scoring_model(self, scoring_model_name: str) -> None:
		obj = self.get_scoring_model(scoring_model_name)
		self._scoring_model_crud.delete(self._require_session(), obj)
