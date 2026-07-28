"""Segment membership (re)computation for ``cdp_segments`` (see
core/models/segmentation.py and docs/PLAN-SEGMENTS-API-IMPROVEMENT.md).

``recompute_segment_membership`` is the shared implementation used by both
the on-demand ``POST /segments/{id}/recompute`` endpoint (core/routers/
segment.py) and the scheduled Dagster job (backend-system/segmentation --
which duplicates this SQL rather than importing this module, since it is a
separately deployed service; keep both in sync if this logic changes).
"""

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import settings
from core.models.segmentation import CdpSegment
from core.utils.sql_safety import validate_sql_where_fragment


def recompute_segment_membership(db: Session, segment: CdpSegment) -> CdpSegment:
    """Re-runs ``segment.sql_rules`` against ``cdp_master_profiles`` (scoped
    to the segment's own tenant), then, in one transaction:

    - Appends ``segment.segment_tag`` to ``segmentation_tags`` for every
      currently-matching active profile that doesn't already have it.
    - Removes ``segment.segment_tag`` from ``segmentation_tags`` for every
      profile in the tenant that has it but no longer matches.
    - Updates ``segment.member_count`` and ``segment.last_computed_at``.

    Raises ``ValueError`` (via ``validate_sql_where_fragment``) if
    ``segment.sql_rules`` fails the injection-safety check -- callers should
    translate that into an HTTP 400, same as the existing matched-profiles
    endpoints.
    """
    if not segment.sql_rules:
        raise ValueError("Segment has no sql_rules to compute")

    where_fragment = validate_sql_where_fragment(segment.sql_rules)
    schema = settings.db_schema
    tenant_id = str(segment.tenant_id)

    matched_rows = db.execute(
        text(
            f"""
            SELECT master_profile_id FROM {schema}.cdp_master_profiles
            WHERE tenant_id = :tenant_id AND status_code = 1 AND ({where_fragment})
            """
        ),
        {"tenant_id": tenant_id},
    ).all()
    matched_ids = [str(row[0]) for row in matched_rows]

    # Add the tag to newly-matching profiles that don't already carry it.
    db.execute(
        text(
            f"""
            UPDATE {schema}.cdp_master_profiles
            SET segmentation_tags = array_append(COALESCE(segmentation_tags, ARRAY[]::text[]), :tag),
                updated_at = now()
            WHERE tenant_id = :tenant_id
              AND master_profile_id = ANY((:matched_ids)::uuid[])
              AND NOT (:tag = ANY(COALESCE(segmentation_tags, ARRAY[]::text[])))
            """
        ),
        {"tenant_id": tenant_id, "matched_ids": matched_ids, "tag": segment.segment_tag},
    )

    # Remove the tag from profiles that carry it but no longer match.
    db.execute(
        text(
            f"""
            UPDATE {schema}.cdp_master_profiles
            SET segmentation_tags = array_remove(segmentation_tags, :tag),
                updated_at = now()
            WHERE tenant_id = :tenant_id
              AND master_profile_id != ALL((:matched_ids)::uuid[])
              AND :tag = ANY(COALESCE(segmentation_tags, ARRAY[]::text[]))
            """
        ),
        {"tenant_id": tenant_id, "matched_ids": matched_ids, "tag": segment.segment_tag},
    )

    segment.member_count = len(matched_ids)
    segment.last_computed_at = datetime.now(timezone.utc)
    db.add(segment)
    db.commit()
    db.refresh(segment)
    return segment
