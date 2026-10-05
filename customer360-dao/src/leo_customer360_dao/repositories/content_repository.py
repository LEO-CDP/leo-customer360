
"""Content items repository: personalized content (news/videos/products/articles)
shown in the Customer 360 profile dashboard, plus recommended content ranking
by segment_tags overlap with master profile segmentation_tags.

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
        master_profile_id: uuid.UUID,
        item_type: Optional[str] = None,
        limit: int = 8,
    ) -> list[dict]:
        """Rank active content items for master_profile_id by how many
        segment_tags overlap with the profile's segmentation_tags (ties broken
        by most-recently published), falling back to domain-matched items with
        no tag overlap when a profile has few/no tags."""
        profile_row = self.session.execute(
            text(
                f"SELECT domain, COALESCE(segmentation_tags, ARRAY[]::text[]) AS tags "
                f"FROM {settings.db_schema}.cdp_master_profiles WHERE master_profile_id = :mpid"
            ),
            {"mpid": str(master_profile_id)},
        ).mappings().first()

        if profile_row is None:
            raise ValueError(f"CdpMasterProfile '{master_profile_id}' not found")

        sql = f"""
            SELECT
                content_item_id, tenant_id, domain, item_type, title, summary, image_url,
                cta_label, cta_url, segment_tags, published_at, status_code, created_at, updated_at,
                ARRAY(SELECT UNNEST(segment_tags) INTERSECT SELECT UNNEST(CAST(:tags AS text[]))) AS matched_tags
            FROM {settings.db_schema}.cdp_content_items
            WHERE status_code = 1
              AND (domain = 'all' OR domain = :domain)
              AND (:item_type IS NULL OR item_type = :item_type)
            ORDER BY cardinality(ARRAY(SELECT UNNEST(segment_tags) INTERSECT SELECT UNNEST(CAST(:tags AS text[])))) DESC,
                     published_at DESC
            LIMIT :limit
        """
        rows = self.session.execute(
            text(sql),
            {
                "tags": list(profile_row["tags"]),
                "domain": profile_row["domain"],
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