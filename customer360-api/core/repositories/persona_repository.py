
"""Personas repository: customer personas, features, scores, and history.

Encapsulates:
- Customer persona CRUD and analytics operations
- Persona features (explainability input signals)
- Persona score details (score breakdown)
- Persona history (audit trail of persona changes)

Uses the same synchronous SQLAlchemy Session as the rest of the API
(see core/database.py).
"""

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.crud import identity as identity_crud
from core.crud.base import CRUDBase
from core.models.identity import (
    CdpCustomerPersona,
    CdpMasterProfile,
    CdpPersonaArchetype,
    CdpPersonaFeature,
    CdpPersonaHistory,
    CdpPersonaScoreDetail,
)


class PersonaRepository:
    """Repository for all persona-related operations."""

    def __init__(self, session: Session):
        self.session = session
        self._archetype_crud = CRUDBase(CdpPersonaArchetype)
        self._persona_crud = CRUDBase(CdpCustomerPersona)
        self._persona_feature_crud = CRUDBase(CdpPersonaFeature)
        self._persona_score_detail_crud = CRUDBase(CdpPersonaScoreDetail)
        self._persona_history_crud = CRUDBase(CdpPersonaHistory)

    # --- Persona Archetypes (shared, reusable; the Persona Management admin
    # UI must list THESE, each with its matched_profile_count) ---

    def list_archetypes(
        self,
        tenant_id: Optional[uuid.UUID] = None,
        domain: Optional[str] = None,
        is_active: Optional[bool] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list:
        """List shared persona archetypes with their matched_profile_count."""
        return identity_crud.list_persona_archetypes_page(
            self.session,
            tenant_id=tenant_id,
            domain=domain,
            is_active=is_active,
            skip=skip,
            limit=limit,
        )

    def get_archetype(self, persona_archetype_id: uuid.UUID) -> Optional[CdpPersonaArchetype]:
        """Get a persona archetype by ID."""
        return self._archetype_crud.get(self.session, persona_archetype_id)

    def create_archetype(self, payload: dict) -> CdpPersonaArchetype:
        """Create a new persona archetype."""
        return self._archetype_crud.create(self.session, payload)

    def update_archetype(self, persona_archetype_id: uuid.UUID, updates: dict) -> CdpPersonaArchetype:
        """Update an existing persona archetype."""
        archetype = self.get_archetype(persona_archetype_id)
        if archetype is None:
            raise ValueError(f"CdpPersonaArchetype '{persona_archetype_id}' not found")
        return self._archetype_crud.update(self.session, archetype, updates)

    def delete_archetype(self, persona_archetype_id: uuid.UUID) -> None:
        """Delete a persona archetype (cascades to its matches)."""
        archetype = self.get_archetype(persona_archetype_id)
        if archetype is None:
            raise ValueError(f"CdpPersonaArchetype '{persona_archetype_id}' not found")
        self._archetype_crud.delete(self.session, archetype)

    def list_master_profiles_by_archetype(
        self,
        persona_archetype_id: uuid.UUID,
        tenant_id: Optional[uuid.UUID] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict:
        """Page of every master profile currently matched to this archetype."""
        return identity_crud.list_master_profiles_by_persona_archetype_page(
            self.session,
            persona_archetype_id=persona_archetype_id,
            tenant_id=tenant_id,
            page=page,
            page_size=page_size,
        )

    # --- Customer Personas (versioned match/assignment rows) ---

    def list_personas(
        self,
        tenant_id: Optional[uuid.UUID] = None,
        domain: Optional[str] = None,
        master_profile_id: Optional[uuid.UUID] = None,
        persona_archetype_id: Optional[uuid.UUID] = None,
        is_active: Optional[bool] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list:
        """List customer persona matches with optional filters."""
        return self._persona_crud.list(
            self.session,
            skip=skip,
            limit=limit,
            tenant_id=tenant_id,
            domain=domain,
            master_profile_id=master_profile_id,
            persona_archetype_id=persona_archetype_id,
            is_active=is_active,
        )

    def get_persona(self, persona_id: uuid.UUID) -> Optional[CdpCustomerPersona]:
        """Get a persona by ID."""
        return self._persona_crud.get(self.session, persona_id)

    def create_persona(self, payload: dict) -> CdpCustomerPersona:
        """Create a new persona."""
        return self._persona_crud.create(self.session, payload)

    def update_persona(self, persona_id: uuid.UUID, updates: dict) -> CdpCustomerPersona:
        """Update an existing persona."""
        persona = self.get_persona(persona_id)
        if persona is None:
            raise ValueError(f"CdpCustomerPersona '{persona_id}' not found")
        return self._persona_crud.update(self.session, persona, updates)

    def delete_persona(self, persona_id: uuid.UUID) -> None:
        """Delete a persona."""
        persona = self.get_persona(persona_id)
        if persona is None:
            raise ValueError(f"CdpCustomerPersona '{persona_id}' not found")
        self._persona_crud.delete(self.session, persona)

    def get_analytics_summary(
        self,
        tenant_id: Optional[uuid.UUID] = None,
        domain: Optional[str] = None,
        is_active: Optional[bool] = None,
        days: int = 90,
    ) -> dict:
        """Get persona analytics summary."""
        from core.repositories.reporting_respository import ReportingRepository

        return ReportingRepository(self.session).persona_analytics_summary(
            tenant_id=tenant_id,
            domain=domain,
            is_active=is_active,
            days=days,
        )

    # --- Persona Features ---

    def list_persona_features(
        self,
        persona_id: Optional[uuid.UUID] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list:
        """List persona features."""
        return self._persona_feature_crud.list(
            self.session, skip=skip, limit=limit, persona_id=persona_id
        )

    def get_persona_feature(self, feature_id: uuid.UUID) -> Optional[CdpPersonaFeature]:
        """Get a persona feature by ID."""
        return self._persona_feature_crud.get(self.session, feature_id)

    def create_persona_feature(self, payload: dict) -> CdpPersonaFeature:
        """Create a new persona feature."""
        return self._persona_feature_crud.create(self.session, payload)

    # --- Persona Score Details ---

    def list_persona_score_details(
        self,
        persona_id: Optional[uuid.UUID] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list:
        """List persona score details."""
        return self._persona_score_detail_crud.list(
            self.session, skip=skip, limit=limit, persona_id=persona_id
        )

    def get_persona_score_detail(self, score_id: uuid.UUID) -> Optional[CdpPersonaScoreDetail]:
        """Get a persona score detail by ID."""
        return self._persona_score_detail_crud.get(self.session, score_id)

    def create_persona_score_detail(self, payload: dict) -> CdpPersonaScoreDetail:
        """Create a new persona score detail."""
        return self._persona_score_detail_crud.create(self.session, payload)

    # --- Persona History ---

    def list_persona_history(
        self,
        persona_id: Optional[uuid.UUID] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list:
        """List persona history entries."""
        return self._persona_history_crud.list(
            self.session, skip=skip, limit=limit, persona_id=persona_id
        )

    def get_persona_history(self, history_id: uuid.UUID) -> Optional[CdpPersonaHistory]:
        """Get a persona history entry by ID."""
        return self._persona_history_crud.get(self.session, history_id)

    def create_persona_history(self, payload: dict) -> CdpPersonaHistory:
        """Create a new persona history entry."""
        return self._persona_history_crud.create(self.session, payload)

    def get_master_profile_by_persona_id(self, persona_id: uuid.UUID) -> Optional[CdpMasterProfile]:
        """Resolve the single master profile linked to a persona row."""
        stmt = (
            select(CdpMasterProfile)
            .join(CdpCustomerPersona, CdpCustomerPersona.master_profile_id == CdpMasterProfile.master_profile_id)
            .where(CdpCustomerPersona.persona_id == persona_id)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_master_profiles_by_persona_category(
        self,
        persona_category: str,
        tenant_id: Optional[uuid.UUID] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict:
        """Page of master profiles whose current active persona has the given category."""
        return identity_crud.list_master_profiles_by_persona_category_page(
            self.session,
            persona_category=persona_category,
            tenant_id=tenant_id,
            page=page,
            page_size=page_size,
        )