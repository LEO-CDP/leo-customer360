"""Daily scheduled entry point for Customer Identity Resolution.

Runs standalone (cron, Airflow PythonOperator/@task, Dagster asset, etc.) and
fully drains the ``cdp_raw_profiles_stage`` staging table in successive
batches by repeatedly calling ``CustomerIdentityResolver.run_resolution_batch()``.
"""

import logging
import os
import sys
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv

from identity_resolution.resolver import CustomerIdentityResolver
from identity_resolution.rls import set_tenant_context

_BACKEND_SYSTEM_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _BACKEND_SYSTEM_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_SYSTEM_ROOT)

from shared.redis_lock import acquire_redis_lease  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "cdp")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")
BATCH_SIZE = max(1, min(int(os.environ.get("CIR_BATCH_SIZE", "500")), 5000))
MAX_BATCHES_PER_RUN = max(1, int(os.environ.get("CIR_MAX_BATCHES_PER_RUN", "10")))
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6580"))
REDIS_DB = int(os.environ.get("REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD")
CIR_LOCK_KEY = "identity-resolution:staging-drain-lock"
CIR_LOCK_TTL_SECONDS = int(os.environ.get("CIR_LOCK_TTL_SECONDS", "3600"))


def build_redis_client():
    """Build the Redis client used to serialize CIR drain runs."""
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


def run_daily_identity_resolution() -> int:
    """Connects to Postgres and drains the staging table until empty.

    Returns:
        The total number of raw profiles processed across all batches.
    """
    redis_client = build_redis_client()
    lease = acquire_redis_lease(redis_client, CIR_LOCK_KEY, CIR_LOCK_TTL_SECONDS)
    if lease is None:
        logger.info("Skipping identity resolution; another drain run owns the Redis lock")
        return 0

    conn = None
    total_processed = 0
    try:
        conn = psycopg2.connect(
            host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, port=DB_PORT
        )
        resolver = CustomerIdentityResolver(
            db_connection=conn,
            schema=DB_SCHEMA,
            batch_size=BATCH_SIZE,
        )
        logger.info("[%s] Starting daily identity resolution run.", datetime.now(timezone.utc))

        for batch_number in range(1, MAX_BATCHES_PER_RUN + 1):
            processed = resolver.run_resolution_batch()
            total_processed += processed
            if processed < BATCH_SIZE:
                break
            lease.refresh()
        else:
            logger.info(
                "CIR run reached its batch budget (%d batches x %d profiles); next run will continue",
                MAX_BATCHES_PER_RUN,
                BATCH_SIZE,
            )

        logger.info(
            "[%s] Daily run complete. Total profiles processed: %d",
            datetime.now(timezone.utc),
            total_processed,
        )
    finally:
        if conn is not None:
            conn.close()
        lease.release()

    return total_processed


def recompute_persona_archetype_match_count(tenant_id: str, persona_archetype_id: str) -> int:
    """Recomputes one archetype's active matched-profile count.

    The count is derived from active ``cdp_customer_personas`` rows, matching
    the database trigger's definition while allowing an API-triggered
    Dagster run to refresh a newly created or edited archetype explicitly.
    """
    conn = psycopg2.connect(
        host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, port=DB_PORT
    )
    try:
        with conn.cursor() as cursor:
            set_tenant_context(cursor, tenant_id)
            cursor.execute(
                f"""
                UPDATE {DB_SCHEMA}.cdp_persona_archetypes
                SET matched_profile_count = (
                    SELECT COUNT(DISTINCT cp.master_profile_id)
                    FROM {DB_SCHEMA}.cdp_customer_personas cp
                    WHERE cp.tenant_id = %(tenant_id)s
                      AND cp.persona_archetype_id = %(persona_archetype_id)s
                      AND cp.is_active = TRUE
                ),
                updated_at = NOW()
                WHERE tenant_id = %(tenant_id)s
                  AND persona_archetype_id = %(persona_archetype_id)s
                RETURNING matched_profile_count
                """,
                {"tenant_id": tenant_id, "persona_archetype_id": persona_archetype_id},
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(
                    f"Persona archetype '{persona_archetype_id}' not found for tenant '{tenant_id}'"
                )
        conn.commit()
        matched_profile_count = int(row[0])
        logger.info(
            "Recomputed persona archetype %s (tenant %s): matched_profile_count=%d",
            persona_archetype_id,
            tenant_id,
            matched_profile_count,
        )
        return matched_profile_count
    finally:
        conn.close()


if __name__ == "__main__":
    run_daily_identity_resolution()
