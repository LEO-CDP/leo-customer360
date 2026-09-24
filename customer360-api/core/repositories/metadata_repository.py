"""Repository and service layer for system metadata endpoints.

Keeps FastAPI transport code out of core metadata/business logic so routers
stay focused on request parsing and HTTP response mapping.
"""

import logging
import smtplib
import socket
import ssl
import uuid
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from core.cache import get_redis_client
from leo_customer360_dao.config import settings
from core.database import engine
from leo_customer360_dao.models.system import SysDomain, SysTenantDomain
from core.utils.dagster_client import DagsterClient

logger = logging.getLogger(__name__)

# Short timeouts keep the metadata endpoint responsive even when a service
# is down; a slow dependency should not make this endpoint hang.
CONNECTIVITY_TIMEOUT_SECONDS = 2

# The default tenant seeded by customer360-database/init-core-database.sql.
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
		"""Create a repository backed by the optional database session."""
		self.session = session

	def _require_session(self) -> Session:
		"""Return the configured session or fail before executing a query."""
		if self.session is None:
			raise MetadataRepositoryError("Database session is required for this operation")
		return self.session

	def _check_postgres(self) -> dict[str, Any]:
		"""Checks that the pooled SQLAlchemy engine can reach PostgreSQL."""
		result = {
			"service": "postgres",
			"status": "unknown"
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
			"status": "unknown"
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
			"status": "unknown"
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

	def _check_smtp(self) -> dict[str, Any]:
		"""Probe the system SMTP relay (the env config the email_engine falls back
		to when a tenant has no active crm_connector_config EMAIL row).

		'disabled' when dispatch is mock or no host is set (no real email is sent).
		Otherwise it opens a connection, optionally STARTTLS + login, and NOOPs to
		verify the credential actually authenticates -- so this validates the
		Brevo/SMTP key, not just TCP reachability. Deliberately NOT part of
		_service_status(): it does a real login and must never run on the
		unauthenticated login-screen /metadata call."""
		result: dict[str, Any] = {
			"service": "smtp",
			"status": "unknown",
			"provider": settings.email_dispatch_adapter,
		}
		if (settings.email_dispatch_adapter or "mock").strip().lower() != "smtp" or not settings.smtp_host:
			result["status"] = "disabled"
			result["note"] = "Email dispatch is 'mock' or no SMTP host is configured (no real email is sent)"
			return result
		result["host"] = settings.smtp_host
		result["port"] = settings.smtp_port
		result["from_address"] = settings.email_from_address
		result["login"] = bool(settings.smtp_username and settings.smtp_password)
		try:
			with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds) as server:
				if settings.smtp_use_tls:
					# Verified TLS (cert + hostname) -- matches the email_engine sender.
					server.starttls(context=ssl.create_default_context())
				if settings.smtp_username and settings.smtp_password:
					server.login(settings.smtp_username, settings.smtp_password)
				server.noop()
			# Connected (and STARTTLS'd) OK. Only claim 'reachable' if we
			# actually logged in -- with no credentials we never tested auth, so
			# report 'no_credentials' instead of implying the relay accepts us
			# (the deploy probe treats anything but reachable/disabled as a warning).
			result["status"] = "reachable" if result["login"] else "no_credentials"
		except Exception as exc:  # noqa: BLE001
			logger.warning("SMTP health check failed", exc_info=True)
			result["status"] = "unreachable"
			# Return a FIXED category, never the raw exception text -- that would
			# leak server internals / stack detail to the caller (CodeQL
			# py/stack-trace-exposure). Full detail is in the server log above.
			if isinstance(exc, smtplib.SMTPAuthenticationError):
				result["error"] = "authentication_failed"
			elif isinstance(exc, ssl.SSLError):
				result["error"] = "tls_error"
			elif isinstance(exc, TimeoutError):
				result["error"] = "timeout"
			elif isinstance(exc, smtplib.SMTPException):
				result["error"] = "smtp_error"
			elif isinstance(exc, OSError):
				result["error"] = "connection_error"
			else:
				result["error"] = "error"
		return result

	def get_smtp_health(self) -> dict[str, Any]:
		"""On-demand SMTP dispatch health for GET /metadata/smtp."""
		return self._check_smtp()

	def _service_status(self) -> dict[str, Any]:
		"""Collect connectivity status for the non-authenticated metadata probe."""
		return {
			"postgres": self._check_postgres(),
			"redis": self._check_redis(),
			"dagster": self._check_dagster(),
		}

	def get_system_metadata(self) -> dict[str, Any]:
		"""Return API configuration and dependency health metadata."""
		services = self._service_status()
		overall = "healthy" if all(
			service["status"] in ("reachable", "disabled") for service in services.values()
		) else "degraded"
		return {
			"service": "customer360-api",
			"api_version": settings.api_version,
			"environment": settings.environment,
			"sso_login": settings.sso_login,
			# Non-secret Keycloak config only -- client_secret never leaves the
			# server (see core/routers/auth_api.py, which owns the code exchange).
			"sso_config": {
				"login_url": settings.sso_login_url,
				"realm": settings.keycloak_realm,
				"client_id": settings.keycloak_client_id,
			},
			"overall_status": overall,
			"services": services,
		}

	def get_dagster_metadata(self) -> dict[str, Any]:
		"""Return Dagster connectivity and configured job metadata."""
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
		"""Return active domains available to the requested tenant."""
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
