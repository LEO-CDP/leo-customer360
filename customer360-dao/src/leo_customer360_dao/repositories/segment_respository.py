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

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, SmallInteger, Text, and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Session

from leo_customer360_dao.config import settings
from leo_customer360_dao.crud.base import CRUDBase
from leo_customer360_dao.crud.segmentation import DOMAIN_ATTRIBUTES_JOIN_SQL, recompute_segment_membership
from leo_customer360_dao.models.identity import CdpMasterProfile, CdpProfileAttribute
from leo_customer360_dao.models.segmentation import CdpSegment
from leo_customer360_dao.schemas.identity import MasterProfileRead


_SEGMENTABLE_SOURCE_TABLES = ("cdp_master_profiles", "cdp_domain_profiles")
_MASTER_PROFILE_COLUMNS = frozenset(CdpMasterProfile.__table__.columns.keys())
_INTEGER_DATA_TYPES = {"SMALLINT", "INTEGER", "INT", "BIGINT", "SERIAL", "BIGSERIAL"}
_NUMERIC_DATA_TYPES = {"NUMERIC", "DECIMAL", "REAL", "FLOAT", "DOUBLE", "NUMBER"}
_DOMAIN_ATTRIBUTE_CASTS = {
    "BOOLEAN": "BOOLEAN",
    "BOOL": "BOOLEAN",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP",
    "TIMESTAMPTZ": "TIMESTAMPTZ",
    "DATETIME": "TIMESTAMP",
    "UUID": "UUID",
}


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
        direct_master_profile_attribute = and_(
            CdpProfileAttribute.attribute_internal_code == CdpProfileAttribute.master_profile_column,
            CdpProfileAttribute.attribute_internal_code.in_(_MASTER_PROFILE_COLUMNS),
        )
        stmt = select(CdpProfileAttribute).where(
            CdpProfileAttribute.is_segmentable.is_(True),
            CdpProfileAttribute.status == "ACTIVE",
            or_(CdpProfileAttribute.source_table.in_(_SEGMENTABLE_SOURCE_TABLES), direct_master_profile_attribute),
        )
        if domain:
            stmt = stmt.where(CdpProfileAttribute.domain_scope.in_(["all", domain]))
        stmt = stmt.order_by(
            func.lower(CdpProfileAttribute.attribute_group),
            CdpProfileAttribute.display_order.asc().nullslast(),
            func.lower(CdpProfileAttribute.name),
            func.lower(CdpProfileAttribute.attribute_internal_code),
        )

        attributes = self.session.execute(stmt).scalars().all()

        def _master_profile_data_type(attribute: CdpProfileAttribute) -> str:
            field = attribute.master_profile_column or attribute.attribute_internal_code
            column = CdpMasterProfile.__table__.columns.get(field)
            if column is None or field != attribute.attribute_internal_code:
                return str(attribute.data_type or "TEXT").upper()

            column_type = column.type
            if isinstance(column_type, Boolean):
                return "BOOLEAN"
            if isinstance(column_type, SmallInteger):
                return "SMALLINT"
            if isinstance(column_type, Integer):
                return "INTEGER"
            if isinstance(column_type, Numeric):
                return "NUMERIC"
            if isinstance(column_type, Date) and not isinstance(column_type, DateTime):
                return "DATE"
            if isinstance(column_type, DateTime):
                return "TIMESTAMP"
            if isinstance(column_type, JSONB):
                return "JSONB"
            if isinstance(column_type, ARRAY):
                return "ARRAY"
            if isinstance(column_type, Text):
                return "TEXT"
            return str(attribute.data_type or "TEXT").upper()

        def _segmentable_field(attribute: CdpProfileAttribute, data_type: str) -> str:
            """SQL-safe field reference for the Audience Builder field picker."""
            if getattr(attribute, "source_table", None) == "cdp_domain_profiles":
                key = str(attribute.attribute_internal_code).replace("'", "''")
                field = f"dp.domain_attributes->>'{key}'"
                cast_type = _DOMAIN_ATTRIBUTE_CASTS.get(data_type)
                if cast_type or data_type in _INTEGER_DATA_TYPES or data_type in _NUMERIC_DATA_TYPES:
                    return f"CAST({field} AS {cast_type or ('INTEGER' if data_type in _INTEGER_DATA_TYPES else 'NUMERIC')})"
                return field
            return attribute.master_profile_column or attribute.attribute_internal_code

        result = []
        for attribute in attributes:
            data_type = _master_profile_data_type(attribute)
            result.append(
                {
                    "field": _segmentable_field(attribute, data_type),
                    "name": attribute.name,
                    "description": attribute.description,
                    "attribute_group": attribute.attribute_group,
                    "data_type": data_type,
                    "domain_scope": attribute.domain_scope,
                    "is_pii": attribute.is_pii,
                }
            )
        return result
