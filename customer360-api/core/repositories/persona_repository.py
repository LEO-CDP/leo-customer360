"""API-layer persona repository that extends the shared DAO implementation."""

import logging
from typing import Optional

from leo_customer360_dao.repositories.persona_repository import PersonaRepository as DaoPersonaRepository

from core.utils.dagster_client import DagsterJobTriggerError, dagster_client

logger = logging.getLogger(__name__)


class PersonaRepository(DaoPersonaRepository):
	"""Provides persona persistence plus asynchronous recompute orchestration."""

	def trigger_recompute(self, persona_archetype, trigger_reason: str) -> Optional[str]:
		"""Best-effort refresh of matched profile counts after archetype changes."""
		try:
			return dagster_client.identity_resolution.recompute_personas(
				trigger_reason=trigger_reason,
				tenant_id=str(persona_archetype.tenant_id),
				persona_archetype_id=str(persona_archetype.persona_archetype_id),
			)
		except DagsterJobTriggerError:
			logger.warning(
				"Could not submit persona recompute for persona_archetype_id=%s",
				persona_archetype.persona_archetype_id,
				exc_info=True,
			)
			return None
