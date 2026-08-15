"""
Ad repository.

Responsible for querying leo_ads.ad.

This repository deliberately contains persistence logic only.
Business decisions such as targeting and ranking belong in services.

Assumptions:
    - Queries filter by tenant_id to prevent cross-tenant data exposure
    - Status filters prevent serving inactive ads (paused, archived)
    - Ordering by score_weight, then ad_id ensures deterministic ranking
    - Redis should normally cache hot placement->ads paths for performance

Performance notes:
    - The hot path (get_active_by_placement) uses indexed fields only
    - Results should be cached in Redis with TTL=300 seconds
    - Do not increase limit beyond 100; use pagination if needed
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from model.ad import Ad


class AdRepository:
    """
    Repository for ad persistence and queries.

    Responsibilities:
        - Retrieve ads by ID
        - Retrieve active ads for a placement (hot path)
        - Serialize ORM objects to API-safe dicts

    Not responsible for:
        - Targeting, ranking, or business logic
        - Caching or Redis
        - Pydantic validation (for now - should migrate later)
    """

    def __init__(
        self,
        engine: Engine,
    ) -> None:
        self.engine = engine

    # ------------------------------------------------------------------
    # Internal session helper
    # ------------------------------------------------------------------

    def _session(self) -> Session:
        """
        Create a short-lived SQLAlchemy session.

        Later this can be replaced by an injected Database/session factory.
        """

        from sqlalchemy.orm import sessionmaker

        session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

        return session_factory()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_by_id(
        self,
        ad_id: int,
    ) -> dict[str, Any] | None:
        """
        Retrieve an ad by primary key.
        """

        session = self._session()

        try:
            statement = (
                select(Ad)
                .where(
                    Ad.ad_id == ad_id,
                    Ad.status == "active",
                )
                .limit(1)
            )

            ad = session.execute(statement).scalar_one_or_none()

            if ad is None:
                return None

            return self._to_dict(ad)

        finally:
            session.close()

    def get_active_by_placement(
        self,
        tenant_id: int,
        placement_id: int,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Retrieve active ads belonging to a placement.

        This uses the indexed hot path:

            tenant_id
            placement_id
            status

        For production serving, Redis should normally answer this query first.
        """

        safe_limit = max(1, min(limit, 100))

        session = self._session()

        try:
            statement = (
                select(Ad)
                .where(
                    Ad.tenant_id == tenant_id,
                    Ad.placement_id == placement_id,
                    Ad.status == "active",
                )
                .order_by(
                    Ad.score_weight.desc(),
                    Ad.ad_id.asc(),
                )
                .limit(safe_limit)
            )

            ads = session.execute(statement).scalars().all()

            return [
                self._to_dict(ad)
                for ad in ads
            ]

        finally:
            session.close()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _to_dict(
        ad: Ad,
    ) -> dict[str, Any]:
        """
        Convert ORM entity to API-safe dictionary.

        Later this should be replaced with Pydantic response models.
        """

        return {
            "ad_id": ad.ad_id,
            "tenant_id": ad.tenant_id,
            "ad_key": ad.ad_key,
            "campaign_id": ad.campaign_id,
            "creative_id": ad.creative_id,
            "placement_id": ad.placement_id,
            "status": ad.status,
            "score_weight": ad.score_weight,
            "frequency_cap": ad.frequency_cap,
            "metadata": ad.metadata_,
            "created_at": ad.created_at,
            "updated_at": ad.updated_at,
        }