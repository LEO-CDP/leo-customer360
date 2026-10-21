"""DAO repository for IAB Audience Taxonomy tags (``customer360.cdp_audience_tags``).

Supports single-row and bulk upserts keyed by ``tag_name``, hierarchical
filtering by IAB tiers / parent_id, free-text search, and validation of
selected ``tag_name`` lists when building segments.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional

from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from leo_customer360_dao.config import settings
from leo_customer360_dao.crud.base import CRUDBase
from leo_customer360_dao.models.segmentation import CdpAudienceTag
from leo_customer360_dao.schemas.segmentation import (
    AudienceTagCreate,
    AudienceTagUpdate,
    normalize_audience_tag_list,
)


class AudienceTagRepository:
    """Data access object for ``customer360.cdp_audience_tags``."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._crud = CRUDBase(CdpAudienceTag)

    @property
    def crud(self) -> CRUDBase[CdpAudienceTag]:
        """Expose generic CRUD helper."""
        return self._crud

    def get_tag(self, tag_name: str) -> Optional[CdpAudienceTag]:
        """Fetch one IAB audience tag by its ``tag_name`` primary key."""
        cleaned = tag_name.strip()
        if not cleaned:
            return None
        return self._crud.get(self.session, cleaned)

    def _build_filter_clauses(
        self,
        *,
        tier_1: Optional[str] = None,
        tier_2: Optional[str] = None,
        tier_3: Optional[str] = None,
        parent_id: Optional[str] = None,
        has_parent: Optional[bool] = None,
        taxonomy_version: Optional[str] = None,
        status_code: Optional[int] = 1,
        q: Optional[str] = None,
    ) -> list[Any]:
        clauses: list[Any] = []
        if status_code is not None:
            clauses.append(CdpAudienceTag.status_code == status_code)
        if taxonomy_version and taxonomy_version.strip():
            clauses.append(CdpAudienceTag.taxonomy_version == taxonomy_version.strip())
        if tier_1 and tier_1.strip():
            clauses.append(CdpAudienceTag.tier_1 == tier_1.strip())
        if tier_2 and tier_2.strip():
            clauses.append(CdpAudienceTag.tier_2 == tier_2.strip())
        if tier_3 and tier_3.strip():
            clauses.append(CdpAudienceTag.tier_3 == tier_3.strip())
        if has_parent is True:
            clauses.append(CdpAudienceTag.parent_id.is_not(None))
            clauses.append(func.btrim(CdpAudienceTag.parent_id) != "")
        elif has_parent is False:
            clauses.append(
                or_(
                    CdpAudienceTag.parent_id.is_(None),
                    func.btrim(CdpAudienceTag.parent_id) == "",
                )
            )
        if parent_id is not None:
            cleaned_parent = parent_id.strip()
            if cleaned_parent == "":
                clauses.append(CdpAudienceTag.parent_id.is_(None))
            else:
                clauses.append(CdpAudienceTag.parent_id == cleaned_parent)
        if q and q.strip():
            pattern = f"%{q.strip()}%"
            clauses.append(
                or_(
                    CdpAudienceTag.tag_name.ilike(pattern),
                    CdpAudienceTag.condensed_name.ilike(pattern),
                    CdpAudienceTag.unique_id.ilike(pattern),
                    CdpAudienceTag.tier_1.ilike(pattern),
                    CdpAudienceTag.tier_2.ilike(pattern),
                    CdpAudienceTag.tier_3.ilike(pattern),
                    CdpAudienceTag.tier_4.ilike(pattern),
                    CdpAudienceTag.tier_5.ilike(pattern),
                    CdpAudienceTag.tier_6.ilike(pattern),
                )
            )
        return clauses

    def list_tags(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        tier_1: Optional[str] = None,
        tier_2: Optional[str] = None,
        tier_3: Optional[str] = None,
        parent_id: Optional[str] = None,
        has_parent: Optional[bool] = None,
        taxonomy_version: Optional[str] = None,
        status_code: Optional[int] = 1,
        q: Optional[str] = None,
    ) -> list[CdpAudienceTag]:
        """List audience taxonomy tags matching optional hierarchy/search filters."""
        clauses = self._build_filter_clauses(
            tier_1=tier_1,
            tier_2=tier_2,
            tier_3=tier_3,
            parent_id=parent_id,
            has_parent=has_parent,
            taxonomy_version=taxonomy_version,
            status_code=status_code,
            q=q,
        )
        stmt = (
            select(CdpAudienceTag)
            .where(*clauses)
            .order_by(CdpAudienceTag.tag_name.asc())
            .offset(max(0, skip))
            .limit(max(1, limit))
        )
        return list(self.session.execute(stmt).scalars().all())

    def count_tags(
        self,
        *,
        tier_1: Optional[str] = None,
        tier_2: Optional[str] = None,
        tier_3: Optional[str] = None,
        parent_id: Optional[str] = None,
        has_parent: Optional[bool] = None,
        taxonomy_version: Optional[str] = None,
        status_code: Optional[int] = 1,
        q: Optional[str] = None,
    ) -> int:
        """Count audience taxonomy tags matching optional hierarchy/search filters."""
        clauses = self._build_filter_clauses(
            tier_1=tier_1,
            tier_2=tier_2,
            tier_3=tier_3,
            parent_id=parent_id,
            has_parent=has_parent,
            taxonomy_version=taxonomy_version,
            status_code=status_code,
            q=q,
        )
        stmt = select(func.count()).select_from(CdpAudienceTag).where(*clauses)
        return int(self.session.execute(stmt).scalar_one())

    @staticmethod
    def _coerce_row_dict(record: AudienceTagCreate | dict[str, Any]) -> dict[str, Any]:
        model = record if isinstance(record, AudienceTagCreate) else AudienceTagCreate.model_validate(record)
        return {
            "tag_name": model.tag_name,
            "unique_id": model.unique_id,
            "parent_id": model.parent_id,
            "taxonomy_version": model.taxonomy_version,
            "condensed_name": model.condensed_name,
            "tier_1": model.tier_1,
            "tier_2": model.tier_2,
            "tier_3": model.tier_3,
            "tier_4": model.tier_4,
            "tier_5": model.tier_5,
            "tier_6": model.tier_6,
            "extension_notes": model.extension_notes,
            "status_code": model.status_code,
        }

    def upsert_tag(
        self,
        record: AudienceTagCreate | dict[str, Any],
        *,
        commit: bool = True,
    ) -> CdpAudienceTag:
        """Upsert one IAB audience taxonomy tag keyed by ``tag_name``."""
        row = self._coerce_row_dict(record)
        stmt = pg_insert(CdpAudienceTag).values([row])
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=[CdpAudienceTag.tag_name],
            set_={
                "unique_id": stmt.excluded.unique_id,
                "parent_id": stmt.excluded.parent_id,
                "taxonomy_version": stmt.excluded.taxonomy_version,
                "condensed_name": stmt.excluded.condensed_name,
                "tier_1": stmt.excluded.tier_1,
                "tier_2": stmt.excluded.tier_2,
                "tier_3": stmt.excluded.tier_3,
                "tier_4": stmt.excluded.tier_4,
                "tier_5": stmt.excluded.tier_5,
                "tier_6": stmt.excluded.tier_6,
                "extension_notes": stmt.excluded.extension_notes,
                "status_code": stmt.excluded.status_code,
                "updated_at": func.now(),
            },
        )
        self.session.execute(upsert_stmt)
        if commit:
            self.session.commit()
        else:
            self.session.flush()
        tag = self.get_tag(row["tag_name"])
        if tag is None:
            return CdpAudienceTag(**row)
        return tag

    def bulk_upsert_tags(
        self,
        records: Sequence[AudienceTagCreate | dict[str, Any]],
        *,
        batch_size: int = 500,
        commit: bool = True,
    ) -> int:
        """Upsert a batch of IAB audience taxonomy rows keyed by ``tag_name``.

        Deduplicates input records by ``tag_name`` (last row wins) before
        executing PostgreSQL ``ON CONFLICT (tag_name) DO UPDATE`` in batches.
        """
        if not records:
            return 0
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")

        deduped_by_tag: dict[str, dict[str, Any]] = {}
        for record in records:
            row = self._coerce_row_dict(record)
            deduped_by_tag[row["tag_name"]] = row

        rows = list(deduped_by_tag.values())
        try:
            for start in range(0, len(rows), batch_size):
                chunk = rows[start : start + batch_size]
                stmt = pg_insert(CdpAudienceTag).values(chunk)
                upsert_stmt = stmt.on_conflict_do_update(
                    index_elements=[CdpAudienceTag.tag_name],
                    set_={
                        "unique_id": stmt.excluded.unique_id,
                        "parent_id": stmt.excluded.parent_id,
                        "taxonomy_version": stmt.excluded.taxonomy_version,
                        "condensed_name": stmt.excluded.condensed_name,
                        "tier_1": stmt.excluded.tier_1,
                        "tier_2": stmt.excluded.tier_2,
                        "tier_3": stmt.excluded.tier_3,
                        "tier_4": stmt.excluded.tier_4,
                        "tier_5": stmt.excluded.tier_5,
                        "tier_6": stmt.excluded.tier_6,
                        "extension_notes": stmt.excluded.extension_notes,
                        "status_code": stmt.excluded.status_code,
                        "updated_at": func.now(),
                    },
                )
                self.session.execute(upsert_stmt)
            if commit:
                self.session.commit()
            else:
                self.session.flush()
        except Exception:
            if commit:
                self.session.rollback()
            raise
        return len(rows)

    def update_tag(
        self,
        tag_name: str,
        payload: AudienceTagUpdate | dict[str, Any],
    ) -> Optional[CdpAudienceTag]:
        """Update an existing audience tag by ``tag_name``."""
        db_obj = self.get_tag(tag_name)
        if db_obj is None:
            return None
        updates = (
            payload.model_dump(exclude_unset=True)
            if isinstance(payload, AudienceTagUpdate)
            else dict(payload)
        )
        return self._crud.update(self.session, db_obj, updates)

    def delete_tag(self, tag_name: str) -> bool:
        """Delete an audience tag by ``tag_name``."""
        db_obj = self.get_tag(tag_name)
        if db_obj is None:
            return False
        self._crud.delete(self.session, db_obj)
        return True

    def validate_tag_names(self, tag_names: Sequence[str]) -> list[str]:
        """Verify that every requested ``tag_name`` exists and is active in ``cdp_audience_tags``."""
        normalized = normalize_audience_tag_list(list(tag_names))
        if not normalized:
            return []

        stmt = select(CdpAudienceTag.tag_name).where(
            CdpAudienceTag.tag_name.in_(normalized),
            CdpAudienceTag.status_code == 1,
        )
        found = set(self.session.execute(stmt).scalars().all())
        missing = [tag for tag in normalized if tag not in found]
        if missing:
            raise ValueError(
                "Unknown or inactive IAB audience tag_name(s): " + ", ".join(missing)
            )
        return normalized

    def sync_profile_audience_tags_from_segments(self, tenant_id: Any) -> int:
        """Recompute ``cdp_master_profiles.audience_tags`` for a tenant from active ``cdp_segments``."""
        schema = settings.db_schema
        result = self.session.execute(
            text(
                f"""
                UPDATE {schema}.cdp_master_profiles AS mp
                SET audience_tags = COALESCE(
                        ARRAY(
                            SELECT DISTINCT u.tag
                            FROM {schema}.cdp_segments AS s
                            CROSS JOIN LATERAL unnest(COALESCE(s.audience_tags, ARRAY[]::text[])) AS u(tag)
                            WHERE s.tenant_id = :tenant_id
                              AND s.is_active = TRUE
                              AND s.status_code = 1
                              AND s.segment_tag = ANY(COALESCE(mp.segmentation_tags, ARRAY[]::text[]))
                              AND u.tag IS NOT NULL
                              AND btrim(u.tag) <> ''
                            ORDER BY u.tag
                        ),
                        ARRAY[]::text[]
                    ),
                    updated_at = now()
                WHERE mp.tenant_id = :tenant_id
                  AND mp.status_code = 1
                """
            ),
            {"tenant_id": str(tenant_id)},
        )
        self.session.commit()
        return int(getattr(result, "rowcount", 0) or 0)
