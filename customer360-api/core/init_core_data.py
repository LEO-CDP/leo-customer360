

"""Startup-time seed/init data for the Customer 360 API.

Seeds default segmentation tags (``cdp_segments``) for every tenant that
doesn't have any yet, upserts the bundled IAB audience taxonomy, and ensures
the development root user has the canonical ``root`` RBAC role.

Called once from ``app.py``'s startup eve-nt. Safe to call on every app
startup: it's idempotent (skips tenants that already have >= 1 segment, and
the ``(tenant_id, segment_tag)`` unique constraint on ``cdp_segments`` is a
second safety net against duplicate inserts under concurrent startups).
"""

import json
import logging
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from leo_customer360_dao.config import settings
from core.database import SessionLocal
from leo_customer360_dao.models.segmentation import CdpSegment
from leo_customer360_dao.models.system import SysUser, SysUserInfo
from core.repositories.metadata_repository import DEFAULT_TENANT_ID
from leo_customer360_dao.schemas.segmentation import build_audience_tag_name, normalize_audience_tag_list
from leo_customer360_dao.utils.security import hash_password

logger = logging.getLogger(__name__)

BASE_DATA_DIR = Path(__file__).with_name("all_base_data")
DEFAULT_SEGMENTS_FILE = BASE_DATA_DIR / "default_segments.json"


def _load_segment_data() -> dict[str, list[dict[str, Any]]]:
    """Load and validate the structured default segment data from JSON."""
    with DEFAULT_SEGMENTS_FILE.open(encoding="utf-8") as data_file:
        data = json.load(data_file)
    if not isinstance(data, dict):
        raise ValueError("Default segment data must be a JSON object")
    required_groups = {
        "COMMON_SEGMENTS",
        "RETAIL_SEGMENTS",
        "ECOMMERCE_SEGMENTS",
        "TRAVEL_SEGMENTS",
        "EDUCATION_SEGMENTS",
        "REAL_ESTATE_SEGMENTS",
        "BANKING_SEGMENTS",
        "MEDIA_SEGMENTS",
    }
    if not required_groups.issubset(data) or any(not isinstance(data[group], list) for group in required_groups):
        raise ValueError("Default segment data has an invalid group structure")
    required_fields = {"segment_tag", "segment_name", "description", "json_rules", "sql_rules"}
    if any(
        not isinstance(segment, dict) or not required_fields.issubset(segment)
        for group in required_groups
        for segment in data[group]
    ):
        raise ValueError("Default segment data contains an invalid segment")
    return data


_SEGMENT_DATA = _load_segment_data()
COMMON_SEGMENTS = _SEGMENT_DATA["COMMON_SEGMENTS"]
RETAIL_SEGMENTS = _SEGMENT_DATA["RETAIL_SEGMENTS"]
ECOMMERCE_SEGMENTS = _SEGMENT_DATA["ECOMMERCE_SEGMENTS"]
TRAVEL_SEGMENTS = _SEGMENT_DATA["TRAVEL_SEGMENTS"]
EDUCATION_SEGMENTS = _SEGMENT_DATA["EDUCATION_SEGMENTS"]
REAL_ESTATE_SEGMENTS = _SEGMENT_DATA["REAL_ESTATE_SEGMENTS"]
BANKING_SEGMENTS = _SEGMENT_DATA["BANKING_SEGMENTS"]
MEDIA_SEGMENTS = _SEGMENT_DATA["MEDIA_SEGMENTS"]


def _with_domain(segments: Sequence[dict[str, Any]], domain: str) -> list[dict[str, Any]]:
    """Return copies of segments with an explicit ``cdp_segments.domain``."""
    return [{**seg, "domain": domain} for seg in segments]


DEFAULT_SEGMENTS: list[dict[str, Any]] = [
    *_with_domain(COMMON_SEGMENTS, "all"),
    *_with_domain(RETAIL_SEGMENTS, "retail"),
    *_with_domain(ECOMMERCE_SEGMENTS, "retail"),
    *_with_domain(TRAVEL_SEGMENTS, "travel"),
    *_with_domain(EDUCATION_SEGMENTS, "education"),
    *_with_domain(REAL_ESTATE_SEGMENTS, "real_estate"),
    *_with_domain(BANKING_SEGMENTS, "banking"),
    *_with_domain(MEDIA_SEGMENTS, "media"),
]


def _normalize_seed_audience_tags(raw_tags: Sequence[str] | None) -> list[str]:
    """Normalize seed ``audience_tags`` to canonical IAB ``tag_name`` slugs."""
    if not raw_tags:
        return []
    converted = [
        build_audience_tag_name(tag.split("|")) if isinstance(tag, str) and "|" in tag else tag
        for tag in raw_tags
        if isinstance(tag, str) and tag.strip()
    ]
    return normalize_audience_tag_list(converted)


def _final_generated_sql(
    sql_rules: str,
    tenant_id: uuid.UUID | None = None,
    audience_tags: Sequence[str] | None = None,
) -> str:
    """Build the tenant-scoped SQL persisted with each default segment."""
    from core.repositories.segment_repository import SegmentRepository

    normalized_tags = _normalize_seed_audience_tags(audience_tags)
    normalized_sql = SegmentRepository.normalize_relative_intervals(sql_rules)
    effective_rules = (
        SegmentRepository.compose_sql_rules_with_audience_tags(normalized_sql, normalized_tags)
        or normalized_sql
    )
    if tenant_id is not None:
        return SegmentRepository.final_generated_sql(effective_rules, tenant_id, normalized_tags)
    where_clause = (
        effective_rules.strip()
        if SegmentRepository._has_wrapping_parentheses(effective_rules)
        else f"({effective_rules.strip()})"
    )
    return (
        f"SELECT master_profile_id FROM {settings.db_schema}.cdp_master_profiles "
        f"WHERE tenant_id = :tenant_id AND {where_clause}"
    )


def list_tenant_ids(db: Session) -> list[uuid.UUID]:
    """Returns all tenant IDs currently present in ``sys_tenant``."""
    return [row[0] for row in db.execute(text(f"SELECT tenant_id FROM {settings.db_schema}.sys_tenant")).all()]


def seed_default_segments_with_breakdown(
    db: Session,
    *,
    tenant_ids: Sequence[uuid.UUID] | None = None,
) -> tuple[int, dict[uuid.UUID, int]]:
    """Backfills missing ``DEFAULT_SEGMENTS`` for each target tenant.

    Unlike a one-time bootstrap, this function is safe for repeated runs in a
    growing SaaS system: if new defaults are introduced later, existing tenants
    receive only the missing tags while custom tenant-defined segments remain
    untouched.
    """
    target_tenant_ids = list(tenant_ids) if tenant_ids is not None else list_tenant_ids(db)

    inserted = 0
    inserted_by_tenant: dict[uuid.UUID, int] = {}
    for tenant_id in target_tenant_ids:
        # Scope this connection to the tenant being seeded before touching
        # any tenant-scoped/RLS-protected table -- same pattern as
        # customer360-backend/identity_resolution's per-row set_config (see resolver.py).
        db.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)})

        existing_tags = {
            row[0]
            for row in db.execute(select(CdpSegment.segment_tag).where(CdpSegment.tenant_id == tenant_id)).all()
        }
        missing_segments = [seg for seg in DEFAULT_SEGMENTS if seg["segment_tag"] not in existing_tags]
        if not missing_segments:
            continue

        from core.repositories.segment_repository import SegmentRepository

        rows_to_insert = []
        for seg in missing_segments:
            raw_payload = {
                "tenant_id": tenant_id,
                "domain": seg["domain"],
                "segment_tag": seg["segment_tag"],
                "audience_tags": _normalize_seed_audience_tags(seg.get("audience_tags")),
                "segment_name": seg["segment_name"],
                "description": seg["description"],
                "json_rules": seg["json_rules"],
                "sql_rules": seg["sql_rules"],
                "processed_by": "human",
            }
            rows_to_insert.append(SegmentRepository.transform_create(raw_payload))

        try:
            result = db.execute(
                pg_insert(CdpSegment)
                .values(rows_to_insert)
                .on_conflict_do_nothing(index_elements=[CdpSegment.tenant_id, CdpSegment.segment_tag])
            )
            db.commit()
            rowcount = getattr(result, "rowcount", None)
            inserted_now = int(rowcount if rowcount is not None else len(rows_to_insert))
            inserted += inserted_now
            inserted_by_tenant[tenant_id] = inserted_now
        except IntegrityError:
            # Another worker/process seeded this tenant concurrently -- safe to skip.
            db.rollback()
            logger.info("Default segments already seeded for tenant %s (concurrent init), skipping.", tenant_id)

    return inserted, inserted_by_tenant


def seed_default_segments(db: Session, *, tenant_ids: Sequence[uuid.UUID] | None = None) -> int:
    """Ensures target tenants have all ``DEFAULT_SEGMENTS``.

    Returns the total number of new segment rows inserted.
    """
    inserted, _ = seed_default_segments_with_breakdown(db, tenant_ids=tenant_ids)
    return inserted


def seed_root_admin_user(db: Session, *, tenant_id: uuid.UUID = DEFAULT_TENANT_ID) -> bool:
    """Ensures DEFAULT_ROOT_USERNAME has a real ``sys_user`` (+ LOCAL
    ``sys_userinfo``) row in ``tenant_id``.

    POST /auth/login (dev mode, SSO_LOGIN=false) authenticates this account
    against DEFAULT_ROOT_USERNAME/PASSWORD, but every other endpoint
    (get_current_user, etc.) requires an actual sys_user row to resolve
    ``request.state.user_id`` against -- without one, the root login worked
    but every subsequent API call 401'd. The password itself lives on
    ``sys_userinfo`` (auth_provider='LOCAL'), matching every other local
    credential -- ``sys_user`` has no password column (see
    database-schema.sql). Idempotent: safe to run on every startup, and keeps
    the hash in sync if DEFAULT_ROOT_PASSWORD changes in .env.
    """
    if not settings.default_root_password:
        return False

    db.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)})
    username = settings.default_root_username.strip().lower()

    try:
        inserted_user = db.execute(
            pg_insert(SysUser)
            .values(
                tenant_id=tenant_id,
                username=username,
                full_name="Root Administrator",
                status="ACTIVE",
            )
            .on_conflict_do_nothing(index_elements=[SysUser.tenant_id, SysUser.username])
            .returning(SysUser.user_id)
        ).first()

        user_id = inserted_user[0] if inserted_user else db.execute(
            select(SysUser.user_id).where(SysUser.tenant_id == tenant_id, SysUser.username == username)
        ).scalar_one()

        schema = settings.db_schema
        db.execute(
            text(
                f"""
                INSERT INTO {schema}.sys_role
                    (tenant_id, role_code, role_name, description, is_system_role, status)
                VALUES
                    (:tenant_id, 'root', 'Root / Super Admin',
                     'Unrestricted platform administration and tenant RBAC control.',
                     TRUE, 'ACTIVE')
                ON CONFLICT (tenant_id, role_code)
                DO UPDATE SET
                    role_name = EXCLUDED.role_name,
                    description = EXCLUDED.description,
                    is_system_role = TRUE,
                    status = 'ACTIVE',
                    updated_at = now()
                """
            ),
            {"tenant_id": str(tenant_id)},
        )
        db.execute(
            text(
                f"""
                INSERT INTO {schema}.sys_user_role (tenant_id, user_id, role_id)
                SELECT :tenant_id, :user_id, role_id
                FROM {schema}.sys_role
                WHERE tenant_id = :tenant_id AND role_code = 'root'
                ON CONFLICT DO NOTHING
                """
            ),
            {"tenant_id": str(tenant_id), "user_id": str(user_id)},
        )

        db.execute(
            pg_insert(SysUserInfo)
            .values(
                tenant_id=tenant_id,
                user_id=user_id,
                auth_provider="LOCAL",
                provider_subject_id=username,
                password_hash=hash_password(settings.default_root_password),
                status="ACTIVE",
            )
            .on_conflict_do_update(
                index_elements=[SysUserInfo.tenant_id, SysUserInfo.auth_provider, SysUserInfo.provider_subject_id],
                set_={"password_hash": hash_password(settings.default_root_password), "updated_at": text("now()")},
            )
        )
        db.commit()
        return inserted_user is not None
    except IntegrityError:
        db.rollback()
        return False


def seed_default_audience_tags(
    db: Session,
    *,
    file_path: str | Path | None = None,
    taxonomy_version: str = "1.1",
) -> int:
    """Parse ``audience_taxonomy1.1.tsv`` and upsert all rows into ``cdp_audience_tags`` by ``tag_name``."""
    from core.repositories.audience_tag_repository import AudienceTagRepository

    result = AudienceTagRepository(db).import_from_tsv_file(
        file_path=file_path,
        taxonomy_version=taxonomy_version,
    )
    return result.upserted_count


def init_core_data() -> None:
    """Runs all startup-time seed/init steps for the API.

    Called during the application startup event so all necessary data is in
    place before the app starts serving requests. Failures are logged and
    swallowed rather than raised, so a seeding issue never prevents the API
    itself from starting.
    """
    logger.info("Initializing core data...")
    db = SessionLocal()
    try:
        inserted = seed_default_segments(db)
        if inserted:
            logger.info("Seeded %d default cdp_segments row(s) across tenant(s).", inserted)
        upserted = seed_default_audience_tags(db)
        if upserted:
            logger.info("Upserted %d default cdp_audience_tags row(s).", upserted)
        if seed_root_admin_user(db):
            logger.info("Seeded root admin sys_user '%s' for tenant %s.", settings.default_root_username, DEFAULT_TENANT_ID)
    except Exception:
        logger.exception("init_core_data failed (continuing startup without seed data)")
    finally:
        db.close()
    logger.info("Core data initialization complete.")
