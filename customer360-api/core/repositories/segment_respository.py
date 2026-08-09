"""Segments/Audience Builder repository: segment rules execution, profile
matching, and membership computation.

Encapsulates:
- Executing segment SQL rules against cdp_master_profiles
- Counting matched profiles for pagination
- Fetching segmentable profile attributes for field picker

Uses the same synchronous SQLAlchemy Session as the rest of the API
(see core/database.py).
"""

import uuid
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from core.config import settings
from core.crud.base import CRUDBase
from core.crud.segmentation import DOMAIN_ATTRIBUTES_JOIN_SQL, recompute_segment_membership
from core.models.identity import CdpProfileAttribute
from core.models.segmentation import CdpSegment
from core.schemas.identity import MasterProfileRead


_SEGMENTABLE_SOURCE_TABLES = ("cdp_master_profiles", "cdp_domain_profiles")


class SegmentRepository:
    def __init__(self, session: Session):
        self.session = session
        self._crud = CRUDBase(CdpSegment)

    def get_segment(self, segment_id: uuid.UUID) -> Optional[CdpSegment]:
        """Get segment by ID."""
        return self._crud.get(self.session, segment_id)

    def get_matched_profiles(
        self, segment_id: uuid.UUID, validated_where_fragment: str, skip: int = 0, limit: int = 50
    ) -> list[dict]:
        """Runs the segment's validated SQL rules against cdp_master_profiles
        and returns matching active profiles.

        Args:
            segment_id: UUID of the segment
            validated_where_fragment: Pre-validated SQL WHERE fragment (from validate_sql_where_fragment)
            skip: Number of results to skip (pagination)
            limit: Maximum number of results to return

        Returns:
            List of matched master profile rows as dicts
        """
        segment = self.get_segment(segment_id)
        if segment is None:
            raise ValueError(f"CdpSegment '{segment_id}' not found")

        if not segment.sql_rules:
            return []

        stmt = text(
            f"""
            SELECT * FROM {settings.db_schema}.cdp_master_profiles
            {DOMAIN_ATTRIBUTES_JOIN_SQL.format(schema=settings.db_schema)}
            WHERE tenant_id = :tenant_id AND status_code = 1 AND ({validated_where_fragment})
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :skip
            """
        )
        rows = self.session.execute(
            stmt, {"tenant_id": str(segment.tenant_id), "limit": limit, "skip": skip}
        ).mappings().all()
        return [dict(row) for row in rows]

    def count_matched_profiles(self, segment_id: uuid.UUID, validated_where_fragment: str) -> int:
        """Count matched profiles for the segment.

        Args:
            segment_id: UUID of the segment
            validated_where_fragment: Pre-validated SQL WHERE fragment

        Returns:
            Count of matching profiles
        """
        segment = self.get_segment(segment_id)
        if segment is None:
            raise ValueError(f"CdpSegment '{segment_id}' not found")

        if not segment.sql_rules:
            return 0

        stmt = text(
            f"""
            SELECT count(*) FROM {settings.db_schema}.cdp_master_profiles
            {DOMAIN_ATTRIBUTES_JOIN_SQL.format(schema=settings.db_schema)}
            WHERE tenant_id = :tenant_id AND status_code = 1 AND ({validated_where_fragment})
            """
        )
        count = self.session.execute(stmt, {"tenant_id": str(segment.tenant_id)}).scalar_one()
        return count

    def recompute_membership(self, segment_id: uuid.UUID) -> dict:
        """Re-runs the segment's SQL rules, updating member_count/last_computed_at
        and syncing segment_tag into/out of cdp_master_profiles.segmentation_tags.

        Args:
            segment_id: UUID of the segment

        Returns:
            Dict with segment_id, member_count, last_computed_at
        """
        segment = self.get_segment(segment_id)
        if segment is None:
            raise ValueError(f"CdpSegment '{segment_id}' not found")

        if not segment.sql_rules:
            raise ValueError("Segment has no sql_rules to compute")

        recompute_segment_membership(self.session, segment)

        return {
            "segment_id": str(segment.segment_id),
            "member_count": segment.member_count,
            "last_computed_at": segment.last_computed_at,
        }

    def get_segmentable_attributes(self, domain: Optional[str] = None) -> list[dict]:
        """Return the catalog of attributes that are valid to reference in a
        segment's SQL rules (Audience Builder field picker).

        Args:
            domain: Optional domain to filter attributes for

        Returns:
            List of attribute dicts with field, name, description, etc.
        """
        stmt = select(CdpProfileAttribute).where(
            CdpProfileAttribute.is_segmentable.is_(True),
            CdpProfileAttribute.status == "ACTIVE",
            CdpProfileAttribute.source_table.in_(_SEGMENTABLE_SOURCE_TABLES),
        )
        if domain:
            stmt = stmt.where(CdpProfileAttribute.domain_scope.in_(["all", domain]))
        stmt = stmt.order_by(CdpProfileAttribute.attribute_group, CdpProfileAttribute.display_order)

        attributes = self.session.execute(stmt).scalars().all()

        def _segmentable_field(attribute: CdpProfileAttribute) -> str:
            """SQL-safe field reference for the Audience Builder field picker."""
            if getattr(attribute, "source_table", None) == "cdp_domain_profiles":
                return f"dp.domain_attributes->>'{attribute.attribute_internal_code}'"
            return attribute.master_profile_column or attribute.attribute_internal_code

        return [
            {
                "field": _segmentable_field(attribute),
                "name": attribute.name,
                "description": attribute.description,
                "attribute_group": attribute.attribute_group,
                "data_type": attribute.data_type,
                "domain_scope": attribute.domain_scope,
                "is_pii": attribute.is_pii,
            }
            for attribute in attributes
        ]
