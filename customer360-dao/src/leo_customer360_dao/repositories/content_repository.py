
"""Repository for tenant-scoped content items and persisted recommendations."""

import logging
import uuid
from typing import Any, Optional

from pydantic import TypeAdapter, ValidationError
from redis.exceptions import RedisError
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from leo_customer360_dao.cache import get_redis_client
from leo_customer360_dao.config import settings
from leo_customer360_dao.crud.base import CRUDBase
from leo_customer360_dao.models.content import CdpContentItem
from leo_customer360_dao.schemas.content import (
    ContentItemCreate,
    ContentItemUpdate,
    RecommendedContentItem,
)

logger = logging.getLogger(__name__)

RECOMMENDATION_CACHE_PREFIX = "content:recommendations:v2"
RECOMMENDATION_ITEMS = TypeAdapter(list[RecommendedContentItem])


class ContentRepository:
    """Encapsulate content-item persistence and recommendation reads."""

    def __init__(self, session: Session):
        self.session = session
        self._crud = CRUDBase(CdpContentItem)

    def list_items(
        self,
        skip: int = 0,
        limit: int | None = None,
        tenant_id: Optional[uuid.UUID] = None,
        domain: Optional[str] = None,
        item_type: Optional[str] = None,
        status_code: Optional[int] = None,
        q: Optional[str] = None,
        content_only: bool = False,
    ) -> list[CdpContentItem]:
        """List content items with tenant, type, status, domain, and text filters."""
        if limit is None:
            limit = settings.api_default_page_size

        conditions = []
        if tenant_id is not None:
            conditions.append(CdpContentItem.tenant_id == tenant_id)
        if domain:
            conditions.append(CdpContentItem.domain == domain)
        if item_type:
            conditions.append(CdpContentItem.item_type == item_type)
        if content_only:
            conditions.append(CdpContentItem.item_type != "product")
        if status_code is not None:
            conditions.append(CdpContentItem.status_code == status_code)
        if q and q.strip():
            pattern = f"%{q.strip()}%"
            conditions.append(
                or_(
                    CdpContentItem.title.ilike(pattern),
                    CdpContentItem.summary.ilike(pattern),
                )
            )
        statement = (
            select(CdpContentItem)
            .where(*conditions)
            .order_by(
                CdpContentItem.updated_at.desc().nullslast(),
                CdpContentItem.content_item_id,
            )
            .offset(skip)
            .limit(limit)
        )
        return list(self.session.scalars(statement).all())

    def get_recommended_items(
        self,
        tenant_id: uuid.UUID,
        master_profile_id: uuid.UUID,
        segment_id: uuid.UUID | None = None,
        item_type: Optional[str] = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """Return cached persisted rankings, optionally filtered to one segment."""
        if limit < 1:
            raise ValueError("limit must be a positive integer")

        redis_client = get_redis_client()
        cache_ttl = settings.cache_ttl_seconds
        cache_key = None
        if redis_client is not None and cache_ttl > 0:
            cache_key = self._recommendation_cache_key(
                redis_client=redis_client,
                tenant_id=tenant_id,
                master_profile_id=master_profile_id,
                segment_id=segment_id,
                item_type=item_type,
                limit=limit,
            )
            if cache_key is not None:
                cached_items = self._get_cached_recommendations(redis_client, cache_key)
                if cached_items is not None:
                    return cached_items

        items = self._load_recommended_items(
            tenant_id=tenant_id,
            master_profile_id=master_profile_id,
            segment_id=segment_id,
            item_type=item_type,
            limit=limit,
        )
        if redis_client is not None and cache_key is not None:
            self._cache_recommendations(redis_client, cache_key, items, cache_ttl)
        return items

    def _load_recommended_items(
        self,
        *,
        tenant_id: uuid.UUID,
        master_profile_id: uuid.UUID,
        segment_id: uuid.UUID | None,
        item_type: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Load persisted rankings; check profile existence only when no rows match."""
        sql = f"""
            WITH profile AS (
                SELECT
                    domain,
                    COALESCE(segmentation_tags, ARRAY[]::text[]) AS tags
                FROM {settings.db_schema}.cdp_master_profiles
                WHERE tenant_id = :tenant_id AND master_profile_id = :mpid
                  AND status_code = 1
            ),
            latest_successful_segment_runs AS (
                SELECT DISTINCT ON (recommendation_run.segment_id)
                    recommendation_run.tenant_id,
                    recommendation_run.segment_id,
                    recommendation_run.run_id
                FROM {settings.db_schema}.cdp_profile_recommendation_runs AS recommendation_run
                WHERE recommendation_run.tenant_id = :tenant_id
                  AND recommendation_run.status = 'SUCCEEDED'
                  AND (
                      CAST(:segment_id AS uuid) IS NULL
                      OR recommendation_run.segment_id = CAST(:segment_id AS uuid)
                  )
                ORDER BY recommendation_run.segment_id,
                         recommendation_run.completed_at DESC,
                         recommendation_run.run_id DESC
            ),
            best_recommendation_per_item AS (
                SELECT DISTINCT ON (content_item_id)
                    recommendation.content_item_id,
                    recommendation.segment_id,
                    recommendation.agent_code,
                    recommendation.rank,
                    recommendation.score,
                    recommendation.semantic_score,
                    recommendation.tag_score,
                    recommendation.strategy,
                    recommendation.matched_tags,
                    recommendation.reason,
                    recommendation.generated_at
                FROM {settings.db_schema}.cdp_profile_recommendations AS recommendation
                JOIN latest_successful_segment_runs AS latest_run
                 ON latest_run.tenant_id = recommendation.tenant_id
                 AND latest_run.segment_id = recommendation.segment_id
                 AND latest_run.run_id = recommendation.run_id
                JOIN {settings.db_schema}.cdp_segments AS segment
                 ON segment.tenant_id = recommendation.tenant_id
                 AND segment.segment_id = recommendation.segment_id
                 AND segment.is_active = TRUE
                JOIN {settings.db_schema}.cdp_agent_workflow AS workflow
                 ON workflow.tenant_id = recommendation.tenant_id
                 AND workflow.segment_id = recommendation.segment_id
                 AND workflow.agent_code = recommendation.agent_code
                 AND workflow.is_active = TRUE
                JOIN {settings.db_schema}.cdp_ai_agents AS agent
                 ON agent.agent_code = recommendation.agent_code
                 AND agent.status = 'ACTIVE'
                 AND agent.model_type = 'ranking_recommendation'
                CROSS JOIN profile
                WHERE recommendation.tenant_id = :tenant_id
                  AND recommendation.master_profile_id = :mpid
                  AND (
                      CAST(:segment_id AS uuid) IS NULL
                      OR recommendation.segment_id = CAST(:segment_id AS uuid)
                  )
                  AND segment.segment_tag = ANY(profile.tags)
                ORDER BY recommendation.content_item_id,
                         recommendation.score DESC,
                         recommendation.rank,
                         recommendation.generated_at DESC,
                         recommendation.segment_id,
                         recommendation.agent_code
            )
                SELECT
                    content.content_item_id,
                    content.tenant_id,
                    content.domain,
                    content.item_type,
                    content.title,
                    content.summary,
                    content.image_url,
                    content.cta_label,
                    content.cta_url,
                    content.segment_tags,
                    content.published_at,
                    content.status_code,
                    content.created_at,
                    content.updated_at,
                    recommendation.matched_tags,
                    recommendation.segment_id,
                    recommendation.agent_code,
                    recommendation.rank,
                    recommendation.score,
                    recommendation.semantic_score,
                    recommendation.tag_score,
                    recommendation.strategy,
                    recommendation.reason,
                    recommendation.generated_at
                FROM best_recommendation_per_item AS recommendation
                JOIN {settings.db_schema}.cdp_content_items AS content
                  ON content.tenant_id = :tenant_id
                 AND content.content_item_id = recommendation.content_item_id
                CROSS JOIN profile
                WHERE content.status_code = 1
                  AND (content.domain = 'all' OR content.domain = profile.domain)
                  AND (:item_type IS NULL OR content.item_type = :item_type)
                ORDER BY
                    recommendation.score DESC,
                    recommendation.rank,
                    recommendation.generated_at DESC,
                    content.content_item_id
                LIMIT :limit
        """
        if settings.environment.lower() in {"dev", "development"}:
            logger.debug("Recommendation SQL: %s", sql)

        rows = self.session.execute(
            text(sql),
            {
                "tenant_id": str(tenant_id),
                "mpid": str(master_profile_id),
                "segment_id": str(segment_id) if segment_id else None,
                "item_type": item_type,
                "limit": limit,
            },
        ).mappings().all()
        if not rows:
            profile_exists = self.session.scalar(
                text(
                    f"SELECT EXISTS (SELECT 1 FROM {settings.db_schema}.cdp_master_profiles "
                    "WHERE tenant_id = :tenant_id AND master_profile_id = :mpid "
                    "AND status_code = 1)"
                ),
                {"tenant_id": str(tenant_id), "mpid": str(master_profile_id)},
            )
            if not profile_exists:
                raise ValueError(f"CdpMasterProfile '{master_profile_id}' not found")

        return [item.model_dump() for item in RECOMMENDATION_ITEMS.validate_python(rows)]

    @staticmethod
    def _recommendation_generation_key(tenant_id: uuid.UUID) -> str:
        return (
            f"{RECOMMENDATION_CACHE_PREFIX}:{settings.db_schema}:"
            f"tenant:{tenant_id}:generation"
        )

    @classmethod
    def _recommendation_cache_key(
        cls,
        *,
        redis_client: Any,
        tenant_id: uuid.UUID,
        master_profile_id: uuid.UUID,
        segment_id: uuid.UUID | None,
        item_type: str | None,
        limit: int,
    ) -> str | None:
        """Build a tenant-scoped key that changes when the tenant cache is invalidated."""
        generation_key = cls._recommendation_generation_key(tenant_id)
        try:
            generation = redis_client.get(generation_key) or "0"
        except RedisError:
            logger.warning("Failed to read recommendation cache generation", exc_info=True)
            return None

        segment_key = str(segment_id) if segment_id is not None else "all"
        item_type_key = item_type or "all"
        return (
            f"{generation_key}:{generation}:"
            f"profile:{master_profile_id}:segment:{segment_key}:type:{item_type_key}:"
            f"limit:{limit}"
        )

    @staticmethod
    def _get_cached_recommendations(
        redis_client: Any,
        cache_key: str,
    ) -> list[dict[str, Any]] | None:
        try:
            cached_json = redis_client.get(cache_key)
        except RedisError:
            logger.warning("Failed to read recommendation cache from Redis", exc_info=True)
            return None
        if cached_json is None:
            return None

        try:
            return [
                item.model_dump()
                for item in RECOMMENDATION_ITEMS.validate_json(cached_json)
            ]
        except ValidationError:
            logger.warning(
                "Invalid recommendation cache payload; removing it",
                exc_info=True,
            )
            try:
                redis_client.delete(cache_key)
            except RedisError:
                logger.warning(
                    "Failed to remove invalid recommendation cache entry",
                    exc_info=True,
                )
            return None

    @staticmethod
    def _cache_recommendations(
        redis_client: Any,
        cache_key: str,
        items: list[dict[str, Any]],
        ttl: int,
    ) -> None:
        payload = RECOMMENDATION_ITEMS.dump_json(
            RECOMMENDATION_ITEMS.validate_python(items)
        )
        try:
            redis_client.set(cache_key, payload, ex=ttl)
        except RedisError:
            logger.warning("Failed to cache recommendations in Redis", exc_info=True)

    @staticmethod
    def invalidate_recommendation_cache(tenant_id: uuid.UUID) -> None:
        """Invalidate cached recommendation lists after tenant content changes."""
        redis_client = get_redis_client()
        if redis_client is None:
            return

        generation_key = ContentRepository._recommendation_generation_key(tenant_id)
        try:
            redis_client.set(
                generation_key,
                uuid.uuid4().hex,
                ex=max(settings.cache_ttl_seconds * 2, 60),
            )
        except RedisError:
            logger.warning("Failed to invalidate recommendation cache in Redis", exc_info=True)

    def count_items(
        self,
        tenant_id: Optional[uuid.UUID] = None,
        domain: Optional[str] = None,
        item_type: Optional[str] = None,
    ) -> int:
        """Count content items matching optional filters."""
        return self._crud.count(
            self.session,
            tenant_id=tenant_id,
            domain=domain,
            item_type=item_type,
        )

    def get_item(
        self,
        content_item_id: uuid.UUID,
        tenant_id: Optional[uuid.UUID] = None,
    ) -> Optional[CdpContentItem]:
        """Get a content item, optionally constrained to its tenant."""
        statement = select(CdpContentItem).where(
            CdpContentItem.content_item_id == content_item_id
        )
        if tenant_id is not None:
            statement = statement.where(CdpContentItem.tenant_id == tenant_id)
        return self.session.scalar(statement)

    def create_item(self, payload: ContentItemCreate) -> CdpContentItem:
        """Create new content item."""
        item = self._crud.create(self.session, payload.model_dump())
        self.invalidate_recommendation_cache(item.tenant_id)
        return item

    def update_item(
        self,
        content_item_id: uuid.UUID,
        payload: ContentItemUpdate,
        tenant_id: Optional[uuid.UUID] = None,
    ) -> Optional[CdpContentItem]:
        """Update existing content item."""
        obj = self.get_item(content_item_id, tenant_id)
        if obj is None:
            return None
        obj_in = payload.model_dump(exclude_unset=True)
        item = self._crud.update(self.session, obj, obj_in)
        self.invalidate_recommendation_cache(item.tenant_id)
        return item

    def delete_item(
        self,
        content_item_id: uuid.UUID,
        tenant_id: Optional[uuid.UUID] = None,
    ) -> bool:
        """Delete content item by ID. Returns True if deleted, False if not found."""
        obj = self.get_item(content_item_id, tenant_id)
        if obj is None:
            return False
        item_tenant_id = obj.tenant_id
        self._crud.delete(self.session, obj)
        self.invalidate_recommendation_cache(item_tenant_id)
        return True