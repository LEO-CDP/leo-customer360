"""Repository for AI-agent metadata and prompt revision history."""

import logging
from datetime import datetime, timezone
from typing import Any

from leo_customer360_dao.crud.base import CRUDBase
from leo_customer360_dao.models.identity import CdpAiAgent

from core.repositories.metadata_repository import (
	MetadataConflictError,
	MetadataNotFoundError,
	MetadataRepository,
	MetadataRepositoryError,
)

logger = logging.getLogger(__name__)


class AiAgentRepository(MetadataRepository):
	"""Encapsulate AI-agent persistence and instruction versioning."""

	def __init__(self, session=None):
		"""Create an AI-agent repository for the supplied database session."""
		super().__init__(session)
		self._crud = CRUDBase(CdpAiAgent)

	def list_ai_agents(self, status: str | None = None, model_type: str | None = None, skip: int = 0, limit: int = 100) -> list[CdpAiAgent]:
		"""Return paginated AI agents ordered by most recent update."""
		try:
			return self._crud.list(self._require_session(), status=status, model_type=model_type, skip=skip, limit=limit, sort_by="updated_at DESC")
		except Exception as exc:  # noqa: BLE001
			logger.warning("Failed to load AI-agent metadata from PostgreSQL", exc_info=True)
			raise MetadataRepositoryError(f"AI-agent metadata unavailable: {exc}") from exc

	def count_ai_agents(self, status: str | None = None, model_type: str | None = None) -> int:
		"""Count AI agents matching the optional filters."""
		try:
			filters: dict[str, Any] = {}
			if status is not None:
				filters["status"] = status
			if model_type is not None:
				filters["model_type"] = model_type
			return self._crud.count(self._require_session(), **filters)
		except Exception as exc:  # noqa: BLE001
			logger.warning("Failed to count AI-agent metadata in PostgreSQL", exc_info=True)
			raise MetadataRepositoryError(f"AI-agent metadata unavailable: {exc}") from exc

	def get_ai_agent(self, agent_code: str) -> CdpAiAgent:
		"""Return one AI agent or raise the repository not-found error."""
		try:
			obj = self._crud.get(self._require_session(), agent_code)
		except Exception as exc:  # noqa: BLE001
			logger.warning("Failed to load AI-agent metadata from PostgreSQL", exc_info=True)
			raise MetadataRepositoryError(f"AI-agent metadata unavailable: {exc}") from exc
		if obj is None:
			raise MetadataNotFoundError(f"CdpAiAgent '{agent_code}' not found")
		return obj

	def create_ai_agent(self, payload: dict[str, Any]) -> CdpAiAgent:
		"""Create an AI agent and initialize its first prompt revision."""
		session = self._require_session()
		if self._crud.get(session, payload["agent_code"]) is not None:
			raise MetadataConflictError(f"CdpAiAgent '{payload['agent_code']}' already exists")
		data = dict(payload)
		if data.get("prompt_key") and not data.get("system_instructions"):
			raise MetadataConflictError("Prompt-backed agents with a prompt_key must specify system_instructions")
		if data.get("system_instructions") and not data.get("prompt_versions"):
			data["prompt_versions"] = [self._prompt_revision(data, 1)]
		return self._crud.create(session, data)

	def update_ai_agent(self, agent_code: str, payload: dict[str, Any]) -> CdpAiAgent:
		"""Update an AI agent and append a revision when instructions change."""
		obj = self.get_ai_agent(agent_code)
		data = dict(payload)
		if "system_instructions" in data and data["system_instructions"] != obj.system_instructions:
			target_version = 1 if not obj.prompt_versions else int(obj.instruction_version or 0) + 1
			revision_payload = {
				**data,
				"required_variables": data.get("required_variables", obj.required_variables or []),
				"instruction_updated_by": data.get("instruction_updated_by", obj.instruction_updated_by or "api"),
				"instruction_note": data.get("instruction_note", obj.instruction_note or "api update"),
			}
			data["prompt_versions"] = list(obj.prompt_versions or []) + [self._prompt_revision(revision_payload, target_version)]
			data["instruction_version"] = target_version
			data["instruction_updated_by"] = revision_payload["instruction_updated_by"]
			data["instruction_note"] = revision_payload["instruction_note"]
		try:
			return self._crud.update(self._require_session(), obj, data)
		except Exception as exc:  # noqa: BLE001
			logger.warning("Failed to update AI-agent metadata in PostgreSQL", exc_info=True)
			raise MetadataRepositoryError(f"AI-agent metadata unavailable: {exc}") from exc

	@staticmethod
	def _prompt_revision(payload: dict[str, Any], version: int) -> dict[str, Any]:
		"""Create a serializable prompt revision from an agent payload."""
		return {
			"version": version,
			"body": payload.get("system_instructions") or "",
			"required_vars": payload.get("required_variables") or [],
			"created_at": datetime.now(timezone.utc).isoformat(),
			"created_by": payload.get("instruction_updated_by") or "api",
			"note": payload.get("instruction_note") or "api update",
		}

	def delete_ai_agent(self, agent_code: str) -> None:
		"""Delete an AI agent after confirming that it exists."""
		obj = self.get_ai_agent(agent_code)
		try:
			self._crud.delete(self._require_session(), obj)
		except Exception as exc:  # noqa: BLE001
			logger.warning("Failed to delete AI-agent metadata from PostgreSQL", exc_info=True)
			raise MetadataRepositoryError(f"AI-agent metadata unavailable: {exc}") from exc