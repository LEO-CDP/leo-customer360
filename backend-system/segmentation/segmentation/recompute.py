"""Segmentation membership recompute logic.

Standalone reimplementation of customer360-api's
``core.crud.segmentation.recompute_segment_membership``, duplicated here
(rather than imported) because this is a separately deployed service with
its own ``requirements.txt``/venv -- see ``backend-system/README.md``'s
"Independent code locations" section and
``docs/api-plans/PLAN-SEGMENTS-API-IMPROVEMENT.md`` Phase 3. Keep the SQL/semantics in
sync with that module if member_count/tag-sync behavior changes.

Used by ``../dagster_defs.py``'s ``recompute_segments_op`` (one full pass
over every ``is_active`` segment, across all tenants) and by
``segmentation_poll_sensor`` (to cheaply check whether anything in
``cdp_master_profiles`` changed since the last poll before requesting a run).
"""

import logging
import os
import re
import sys
from typing import Any, Callable, Optional

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

from .rls import set_tenant_context

_BACKEND_SYSTEM_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _BACKEND_SYSTEM_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_SYSTEM_ROOT)

from shared.redis_lock import acquire_redis_lease  # noqa: E402

load_dotenv()

logger = logging.getLogger(__name__)

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "customer360")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6580"))
REDIS_DB = int(os.environ.get("REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD")
SEGMENTATION_LOCK_KEY = "segmentation:recompute-run-lock"
SEGMENTATION_LOCK_TTL_SECONDS = int(
    os.environ.get("SEGMENTATION_LOCK_TTL_SECONDS", "1800")
)

# Defense-in-depth mirror of customer360-api's core/utils/sql_safety.py
# validate_sql_where_fragment(): this service doesn't import customer360-api
# code (separate deployable), so segments with a missing/unsafe sql_rules are
# simply skipped here (already rejected at write-time by the API's own
# validation -- this is a second line of defense against rows written
# directly, e.g. via a migration/seed script).
_STACKING_OR_COMMENT_PATTERN = re.compile(r";|--|/\*|\*/|#")
_DML_DDL_PATTERN = re.compile(
    r"\b(insert|update|delete|drop|alter|grant|revoke|truncate|create|exec|execute|call|copy|"
    r"vacuum|reindex|cluster|analyze|explain|listen|notify|unlisten|prepare|deallocate|"
    r"declare|fetch|commit|rollback|savepoint|begin|start|lock|merge|do|"
    r"set|reset|show|comment|import|foreign)\b",
    re.IGNORECASE,
)
_QUERY_KEYWORDS_PATTERN = re.compile(r"\b(select|from|join|union|into|with)\b", re.IGNORECASE)

# Exposes ONLY cdp_domain_profiles.domain_attributes, aliased as "dp", scoped
# to the current cdp_master_profiles row's own (tenant_id, master_profile_id,
# domain) -- no other cdp_domain_profiles column is exposed (several share
# names with cdp_master_profiles: tenant_id, lifecycle_stage, persona_summary,
# engagement_score, status_code, created_at, updated_at), so existing
# bare-column sql_rules fragments keep resolving unambiguously against
# cdp_master_profiles. New domain-scoped rules reference it explicitly, e.g.
# dp.domain_attributes->>'risk_segment'. Mirrors customer360-api's
# core/crud/segmentation.py::DOMAIN_ATTRIBUTES_JOIN_SQL -- keep both in sync.
_DOMAIN_ATTRIBUTES_JOIN_SQL = """
    LEFT JOIN LATERAL (
        SELECT dom.domain_attributes
        FROM {schema}.cdp_domain_profiles dom
        JOIN {schema}.sys_domain sd ON sd.domain_id = dom.domain_id
        WHERE dom.tenant_id = cdp_master_profiles.tenant_id
          AND dom.master_profile_id = cdp_master_profiles.master_profile_id
          AND sd.domain_code = cdp_master_profiles.domain
        LIMIT 1
    ) dp ON TRUE
"""


def _is_safe_where_fragment(fragment: Optional[str]) -> bool:
    if not fragment or not fragment.strip():
        return False
    if _STACKING_OR_COMMENT_PATTERN.search(fragment):
        return False
    if _DML_DDL_PATTERN.search(fragment):
        return False
    if _QUERY_KEYWORDS_PATTERN.search(fragment):
        return False
    return True


def _connect():
    return psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, port=DB_PORT)


def build_redis_client():
    """Build the Redis client used to serialize full segment recomputes."""
    import redis

    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
    )


def _list_tenant_ids(cursor) -> list[str]:
    """List tenant IDs from the global catalog before tenant-scoped work."""
    set_tenant_context(cursor, None)
    cursor.execute(f"SELECT tenant_id FROM {DB_SCHEMA}.sys_tenant ORDER BY tenant_id")
    return [str(row["tenant_id"] if isinstance(row, dict) else row[0]) for row in cursor.fetchall()]


def _recompute_one_segment(conn, *, tenant_id: str, segment_tag: str, where_fragment: str, segment_id: str) -> int:
    """Re-runs where_fragment against cdp_master_profiles for one tenant,
    syncs segment_tag into/out of segmentation_tags, and writes back
    member_count/last_computed_at onto the cdp_segments row. Returns the
    matched member count."""
    with conn.cursor() as cur:
        set_tenant_context(cur, tenant_id)
        cur.execute(
            f"""
            CREATE TEMP TABLE IF NOT EXISTS _c360_segment_matches (
                master_profile_id UUID PRIMARY KEY
            ) ON COMMIT DROP
            """
        )
        cur.execute("TRUNCATE _c360_segment_matches")
        cur.execute(
            f"""
            INSERT INTO _c360_segment_matches (master_profile_id)
            SELECT master_profile_id FROM {DB_SCHEMA}.cdp_master_profiles
            {_DOMAIN_ATTRIBUTES_JOIN_SQL.format(schema=DB_SCHEMA)}
            WHERE tenant_id = %(tenant_id)s AND status_code = 1 AND ({where_fragment})
            """,
            {"tenant_id": tenant_id},
        )

        # Add the tag to newly-matching profiles that don't already carry it.
        cur.execute(
            f"""
            UPDATE {DB_SCHEMA}.cdp_master_profiles AS profiles
            SET segmentation_tags = array_append(COALESCE(segmentation_tags, ARRAY[]::text[]), %(tag)s),
                updated_at = now()
            FROM _c360_segment_matches AS matches
            WHERE profiles.tenant_id = %(tenant_id)s
              AND profiles.master_profile_id = matches.master_profile_id
              AND NOT (%(tag)s = ANY(COALESCE(segmentation_tags, ARRAY[]::text[])))
            """,
            {"tenant_id": tenant_id, "tag": segment_tag},
        )

        # Remove the tag from profiles that carry it but no longer match.
        cur.execute(
            f"""
            UPDATE {DB_SCHEMA}.cdp_master_profiles AS profiles
            SET segmentation_tags = array_remove(segmentation_tags, %(tag)s),
                updated_at = now()
            WHERE profiles.tenant_id = %(tenant_id)s
              AND %(tag)s = ANY(COALESCE(profiles.segmentation_tags, ARRAY[]::text[]))
              AND NOT EXISTS (
                  SELECT 1
                  FROM _c360_segment_matches AS matches
                  WHERE matches.master_profile_id = profiles.master_profile_id
              )
            """,
            {"tenant_id": tenant_id, "tag": segment_tag},
        )

        cur.execute(
            "SELECT COUNT(*) FROM _c360_segment_matches"
        )
        row = cur.fetchone()

        cur.execute(
            f"""
            UPDATE {DB_SCHEMA}.cdp_segments
            SET member_count = %(member_count)s, last_computed_at = now(), updated_at = now()
            WHERE tenant_id = %(tenant_id)s AND segment_id = %(segment_id)s
            """,
            {
                "tenant_id": tenant_id,
                "member_count": int(row[0] if row else 0),
                "segment_id": segment_id,
            },
        )

    return int(row[0] if row else 0)


def recompute_all_active_segments(
    tenant_id: Optional[str] = None,
    segment_id: Optional[str] = None,
    log: Optional[Callable[..., None]] = None,
    redis_client: Optional[Any] = None,
    _lock_acquired: bool = False,
    _lease: Optional[Any] = None,
) -> dict[str, Any]:
    """Recomputes member_count/segmentation_tags for every ``is_active =
    true`` segment, optionally scoped to a single tenant and segment. This is the
    full-scan batch job counterpart to customer360-api's on-demand
    ``POST /segments/{id}/recompute``.

    Args:
        tenant_id: if provided, only segments belonging to this tenant are
            recomputed -- this is how customer360-api's on-demand
            ``POST /segments/admin/recompute-all`` (triggered by the admin
            UI's "Refresh" button) scopes the job to the caller's own
            tenant instead of recomputing every tenant's segments. ``None``
            (the default) recomputes across ALL tenants -- used by
            ``segmentation_poll_sensor``'s scheduled full pass.
        segment_id: if provided, only this active segment is recomputed. It
            must be accompanied by ``tenant_id`` to keep targeted runs
            tenant-scoped.

    Returns:
        ``{"tenant_id": str | None, "segments_processed": int,
        "segments_skipped": int, "total_members": int}``
    """
    if tenant_id is not None and not tenant_id:
        raise ValueError("tenant_id cannot be empty")
    if segment_id is not None and not segment_id:
        raise ValueError("segment_id cannot be empty")
    if segment_id is not None and tenant_id is None:
        raise ValueError("tenant_id is required when segment_id is provided")

    if not _lock_acquired:
        cache = redis_client if redis_client is not None else build_redis_client()
        lease = acquire_redis_lease(
            cache,
            SEGMENTATION_LOCK_KEY,
            SEGMENTATION_LOCK_TTL_SECONDS,
        )
        if lease is None:
            (log or logger.info)(
                "Skipping segmentation recompute; another run owns the Redis lock"
            )
            return {
                "tenant_id": tenant_id,
                "segments_processed": 0,
                "segments_skipped": 0,
                "total_members": 0,
            }
        try:
            return recompute_all_active_segments(
                tenant_id=tenant_id,
                segment_id=segment_id,
                log=log,
                redis_client=cache,
                _lock_acquired=True,
                _lease=lease,
            )
        finally:
            lease.release()

    conn = _connect()
    segments_processed = 0
    segments_skipped = 0
    total_members = 0
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            tenant_ids = [tenant_id] if tenant_id is not None else _list_tenant_ids(cur)
            segments = []
            for current_tenant_id in tenant_ids:
                set_tenant_context(cur, current_tenant_id)
                query = f"""
                    SELECT segment_id, tenant_id, segment_tag, sql_rules
                    FROM {DB_SCHEMA}.cdp_segments
                    WHERE is_active = true
                      AND status_code = 1
                      AND tenant_id = %(tenant_id)s
                """
                params: dict[str, str] = {"tenant_id": current_tenant_id}
                if segment_id is not None:
                    query += " AND segment_id = %(segment_id)s"
                    params["segment_id"] = segment_id
                cur.execute(query, params)
                segments.extend(cur.fetchall())

        for segment in segments:
            if _lease is not None:
                _lease.refresh()
            sql_rules = segment["sql_rules"]
            if not _is_safe_where_fragment(sql_rules):
                segments_skipped += 1
                logger.warning(
                    "Skipping segment %s (tenant %s): missing/unsafe sql_rules",
                    segment["segment_id"],
                    segment["tenant_id"],
                )
                continue

            member_count = _recompute_one_segment(
                conn,
                tenant_id=str(segment["tenant_id"]),
                segment_tag=segment["segment_tag"],
                where_fragment=sql_rules,
                segment_id=str(segment["segment_id"]),
            )
            segments_processed += 1
            total_members += member_count
            (log or logger.info)(
                "Recomputed segment %s (tenant %s): member_count=%d",
                segment["segment_id"],
                segment["tenant_id"],
                member_count,
            )

        conn.commit()
    finally:
        conn.close()

    return {
        "tenant_id": tenant_id,
        "segments_processed": segments_processed,
        "segments_skipped": segments_skipped,
        "total_members": total_members,
    }


def count_recently_changed_master_profiles(since_iso: Optional[str]) -> int:
    """Returns how many ``cdp_master_profiles`` rows were created/updated
    since ``since_iso`` (an ISO-8601 timestamp), or the total row count if
    ``since_iso`` is ``None`` (first-ever sensor tick). Used by
    ``segmentation_poll_sensor`` (``../dagster_defs.py``) to only launch a
    recompute run when something actually changed, instead of firing
    unconditionally every poll interval."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            total = 0
            for tenant_id in _list_tenant_ids(cur):
                set_tenant_context(cur, tenant_id)
                if since_iso is None:
                    cur.execute(
                        f"SELECT count(*) FROM {DB_SCHEMA}.cdp_master_profiles WHERE tenant_id = %(tenant_id)s",
                        {"tenant_id": tenant_id},
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT count(*) FROM {DB_SCHEMA}.cdp_master_profiles
                        WHERE tenant_id = %(tenant_id)s
                          AND (created_at > %(since)s OR updated_at > %(since)s)
                        """,
                        {"tenant_id": tenant_id, "since": since_iso},
                    )
                row = cur.fetchone()
                total += row[0] if row else 0
            return total
    finally:
        conn.close()
