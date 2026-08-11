

"""Startup-time seed/init data for the Customer 360 API.

Currently seeds a small set of default segmentation tags (``cdp_segments``)
for every tenant that doesn't have any yet, so a fresh install already has a
usable Audience Builder starting point instead of an empty segment list.

Called once from ``app.py``'s startup eve-nt. Safe to call on every app
startup: it's idempotent (skips tenants that already have >= 1 segment, and
the ``(tenant_id, segment_tag)`` unique constraint on ``cdp_segments`` is a
second safety net against duplicate inserts under concurrent startups).
"""

import logging
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.config import settings
from core.database import SessionLocal
from core.models.segmentation import CdpSegment
from core.models.system import SysUser, SysUserInfo
from core.repositories.metadata_repository import DEFAULT_TENANT_ID
from core.utils.security import hash_password

logger = logging.getLogger(__name__)

# System-default segments seeded for every new tenant. json_rules mirrors the
# jQuery QueryBuilder rule tree an admin would build in the UI; sql_rules is
# the equivalent translated WHERE-clause fragment against cdp_master_profiles.
DEFAULT_SEGMENTS: list[dict[str, Any]] = [
    {
        "segment_tag": "new_customer",
        "segment_name": "New Customers",
        "description": "Profiles that became a paying customer in the last 30 days.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "customer_since", "operator": "greater_or_equal", "value": "-30 days"}],
        },
        "sql_rules": "customer_since >= (CURRENT_DATE - INTERVAL '30 days')",
    },
    {
        "segment_tag": "high_value",
        "segment_name": "High-Value Customers",
        "description": "Profiles with predictive customer lifetime value above 1000.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "predictive_clv", "operator": "greater", "value": 1000}],
        },
        "sql_rules": "predictive_clv > 1000",
    },
    {
        "segment_tag": "churn_risk",
        "segment_name": "At Risk of Churn",
        "description": "Profiles with a high or critical churn risk tier.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "churn_risk_tier", "operator": "in", "value": ["high", "critical"]}],
        },
        "sql_rules": "churn_risk_tier IN ('high', 'critical')",
    },
    {
        "segment_tag": "dormant",
        "segment_name": "Dormant Profiles",
        "description": "Profiles with no activity in the last 90 days.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "last_activity_at", "operator": "less", "value": "-90 days"}],
        },
        "sql_rules": "last_activity_at < (now() - INTERVAL '90 days')",
    },
    {
        "segment_tag": "recently_active",
        "segment_name": "Recently Active",
        "description": "Profiles active in the last 30 days.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "last_activity_at", "operator": "greater_or_equal", "value": "-30 days"}],
        },
        "sql_rules": "last_activity_at >= (now() - INTERVAL '30 days')",
    },
    {
        "segment_tag": "growth_potential",
        "segment_name": "Growth Potential",
        "description": "Mid-value profiles with room to grow into high-value customers.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "predictive_clv", "operator": "greater_or_equal", "value": 500},
                {"field": "predictive_clv", "operator": "less", "value": 1001},
            ],
        },
        "sql_rules": "predictive_clv >= 500 AND predictive_clv < 1001",
    },
    {
        "segment_tag": "win_back",
        "segment_name": "Win-Back Candidates",
        "description": "Profiles inactive for 30-180 days with elevated churn risk.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "last_activity_at", "operator": "less", "value": "-30 days"},
                {"field": "last_activity_at", "operator": "greater", "value": "-180 days"},
                {"field": "churn_risk_tier", "operator": "in", "value": ["medium", "high", "critical"]},
            ],
        },
        "sql_rules": (
            "last_activity_at < (now() - INTERVAL '30 days') "
            "AND last_activity_at > (now() - INTERVAL '180 days') "
            "AND churn_risk_tier IN ('medium', 'high', 'critical')"
        ),
    },
    {
        "segment_tag": "champions",
        "segment_name": "Champions",
        "description": "Long-tenure, top-value customers to prioritize for loyalty experiences.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "predictive_clv", "operator": "greater", "value": 2500},
                {"field": "customer_since", "operator": "less", "value": "-365 days"},
            ],
        },
        "sql_rules": "predictive_clv > 2500 AND customer_since < (CURRENT_DATE - INTERVAL '365 days')",
    },
]


def _final_generated_sql(sql_rules: str) -> str:
    return (
        f"SELECT master_profile_id FROM {settings.db_schema}.cdp_master_profiles "
        f"WHERE tenant_id = :tenant_id AND ({sql_rules})"
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
        # backend-system/identity_resolution's per-row set_config (see resolver.py).
        db.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)})

        existing_tags = {
            row[0]
            for row in db.execute(select(CdpSegment.segment_tag).where(CdpSegment.tenant_id == tenant_id)).all()
        }
        missing_segments = [seg for seg in DEFAULT_SEGMENTS if seg["segment_tag"] not in existing_tags]
        if not missing_segments:
            continue

        rows_to_insert = [
            {
                "tenant_id": tenant_id,
                "segment_tag": seg["segment_tag"],
                "segment_name": seg["segment_name"],
                "description": seg["description"],
                "json_rules": seg["json_rules"],
                "sql_rules": seg["sql_rules"],
                "final_generated_sql": _final_generated_sql(seg["sql_rules"]),
                "processed_by": "human",
            }
            for seg in missing_segments
        ]

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
        if seed_root_admin_user(db):
            logger.info("Seeded root admin sys_user '%s' for tenant %s.", settings.default_root_username, DEFAULT_TENANT_ID)
    except Exception:
        logger.exception("init_core_data failed (continuing startup without seed data)")
    finally:
        db.close()
    logger.info("Core data initialization complete.")
