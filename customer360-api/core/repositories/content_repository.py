
"""Content items repository: personalized content (news/videos/products/articles)
shown in the Customer 360 profile dashboard, plus recommended content ranking
by segment_tags overlap with master profile segmentation_tags.

Uses the same synchronous SQLAlchemy Session as the rest of the API
(see core/database.py).
"""

import uuid
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from core.config import settings
from core.crud.base import CRUDBase
from core.models.content import CdpContentItem
from core.schemas.content import ContentItemCreate, ContentItemRead, ContentItemUpdate


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
    ) -> list[CdpContentItem]:
        """List all content items with optional filters."""
        if limit is None:
            limit = settings.api_default_page_size
        return self._crud.list(
            self.session,
            skip=skip,
            limit=limit,
            tenant_id=tenant_id,
            domain=domain,
            item_type=item_type,
        )

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

    def get_item(self, content_item_id: uuid.UUID) -> Optional[CdpContentItem]:
        """Get content item by ID."""
        return self._crud.get(self.session, content_item_id)

    def create_item(self, payload: ContentItemCreate) -> CdpContentItem:
        """Create new content item."""
        return self._crud.create(self.session, payload.model_dump())

    def update_item(
        self, content_item_id: uuid.UUID, payload: ContentItemUpdate
    ) -> Optional[CdpContentItem]:
        """Update existing content item."""
        obj = self._crud.get(self.session, content_item_id)
        if obj is None:
            return None
        obj_in = payload.model_dump(exclude_unset=True)
        return self._crud.update(self.session, obj, obj_in)

    def delete_item(self, content_item_id: uuid.UUID) -> bool:
        """Delete content item by ID. Returns True if deleted, False if not found."""
        obj = self._crud.get(self.session, content_item_id)
        if obj is None:
            return False
        self._crud.delete(self.session, obj)
        return True