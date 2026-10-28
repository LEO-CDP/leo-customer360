"""
Placement repository.

Responsible for querying leo_ads.placement.

Placements represent publisher inventory slots. The serving layer uses placement
lookup to retrieve available ad candidates for a specific slot (e.g., homepage_top).

Key separation:
    - Placement: Publisher inventory definition (dimensions, location, format)
    - Placement Format: Supported formats/capabilities for a placement
    - Ad: Business logic + content linking (placement -> ads via Ad table)

Performance notes:
    - Placement lookups should be cached in Redis (TTL=3600 seconds)
    - Placement-to-format lookup is embedded in PlacementFormat table
    - Consider pre-computing compatible ad candidates in Redis for hot placements

Multi-tenancy:
    - Tenant filtering is optional but should become mandatory
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from model.placement import Placement


class PlacementRepository:
    """
    Repository for publisher inventory placements.

    Responsibilities:
        - Retrieve active placements by placement_key
        - Optional tenant filtering for multi-tenancy
        - Serialize ORM objects to API-safe dicts

    Not responsible for:
        - Validating placement constraints (width/height/responsive)
        - Fetching associated formats (PlacementFormat)
        - Fetching candidate ads (use AdRepository)
        - Caching (should migrate to RedisRepository)
    """

    def __init__(
        self,
        engine: Engine,
    ) -> None:
        self.engine = engine

        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

    def _session(self) -> Session:
        return self.session_factory()

    def get_active_by_key(
        self,
        placement_key: str,
        tenant_id: int | None = None,
    ) -> dict[str, Any] | None:
        """
        Retrieve an active placement.

        Tenant filtering is optional for now but should become mandatory once
        tenant authentication middleware is wired into the API.
        """

        session = self._session()

        try:
            conditions = [
                Placement.placement_key == placement_key,
                Placement.status == "active",
            ]

            if tenant_id is not None:
                conditions.append(
                    Placement.tenant_id == tenant_id,
                )

            statement = (
                select(Placement)
                .where(*conditions)
                .limit(1)
            )

            placement = (
                session.execute(statement)
                .scalar_one_or_none()
            )

            if placement is None:
                return None

            return self._to_dict(placement)

        finally:
            session.close()

    @staticmethod
    def _to_dict(
        placement: Placement,
    ) -> dict[str, Any]:
        return {
            "placement_id": placement.placement_id,
            "tenant_id": placement.tenant_id,
            "placement_key": placement.placement_key,
            "name": placement.name,
            "status": placement.status,
            "min_width_px": placement.min_width_px,
            "max_width_px": placement.max_width_px,
            "min_height_px": placement.min_height_px,
            "max_height_px": placement.max_height_px,
            "responsive": placement.responsive,
            "metadata": placement.metadata_,
            "created_at": placement.created_at,
            "updated_at": placement.updated_at,
        }