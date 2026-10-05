
"""Content items repository: personalized content (news/videos/products/articles)
shown in the Customer 360 profile dashboard, plus persisted workflow-ranked
recommendations with optional segment filtering.

Uses the same synchronous SQLAlchemy Session as the rest of the API
(see core/database.py).
"""

import uuid
from typing import Optional

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from leo_customer360_dao.config import settings
from leo_customer360_dao.crud.base import CRUDBase
from leo_customer360_dao.models.content import CdpContentItem
from leo_customer360_dao.schemas.content import ContentItemCreate, ContentItemRead, ContentItemUpdate


class ContentRepository:
    def __init__(self, session: Session):
        self.session = session
        self._crud = CRUDBase(CdpContentItem)

    def list_items(
        self,
        skip: int = 0,
        limit: int = None,
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
            .order_by(CdpContentItem.updated_at.desc().nullslast(), CdpContentItem.content_item_id)
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
    ) -> list[dict]:
        """Return persisted rankings, optionally filtered to one segment."""
        profile_row = self.session.execute(
            text(
                f"SELECT domain FROM {settings.db_schema}.cdp_master_profiles "
                "WHERE tenant_id = :tenant_id AND master_profile_id = :mpid "
                "AND status_code = 1"
            ),
            {"tenant_id": str(tenant_id), "mpid": str(master_profile_id)},
        ).mappings().first()

        if profile_row is None:
            raise ValueError(f"CdpMasterProfile '{master_profile_id}' not found")

        sql = f"""
            WITH profile AS (
                SELECT domain, COALESCE(segmentation_tags, ARRAY[]::text[]) AS tags
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
                JOIN {settings.db_schema}.cdp_segments AS segment
                  ON segment.tenant_id = recommendation_run.tenant_id
                 AND segment.segment_id = recommendation_run.segment_id
                 AND segment.is_active = TRUE
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
            eligible_recommendations AS (
                SELECT
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
            ),
            best_recommendation_per_item AS (
                SELECT DISTINCT ON (content_item_id)
                    content_item_id, segment_id, agent_code, rank, score,
                    semantic_score, tag_score, strategy, matched_tags, reason, generated_at
                FROM eligible_recommendations
                ORDER BY content_item_id, score DESC, rank, generated_at DESC,
                         segment_id, agent_code
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
            ORDER BY recommendation.score DESC, recommendation.rank,
                     recommendation.generated_at DESC, content.content_item_id
            LIMIT :limit
        """
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
        return [dict(row) for row in rows]

    def count_items(
        self,
        tenant_id: Optional[uuid.UUID] = None,
        domain: Optional[str] = None,
        item_type: Optional[str] = None,
    ) -> int:
        """Count content items matching optional filters."""
        return self._crud.count(self.session, tenant_id=tenant_id, domain=domain, item_type=item_type)

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
        return self._crud.create(self.session, payload.model_dump())

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
        return self._crud.update(self.session, obj, obj_in)

    def delete_item(
        self,
        content_item_id: uuid.UUID,
        tenant_id: Optional[uuid.UUID] = None,
    ) -> bool:
        """Delete content item by ID. Returns True if deleted, False if not found."""
        obj = self.get_item(content_item_id, tenant_id)
        if obj is None:
            return False
        self._crud.delete(self.session, obj)
        return True