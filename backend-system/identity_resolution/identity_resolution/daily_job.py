"""Daily scheduled entry point for Customer Identity Resolution.

Runs standalone (cron, Airflow PythonOperator/@task, Dagster asset, etc.) and
fully drains the ``cdp_raw_profiles_stage`` staging table in successive
batches by repeatedly calling ``CustomerIdentityResolver.run_resolution_batch()``.
"""

import logging
import os
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

from identity_resolution.resolver import CustomerIdentityResolver
from identity_resolution.rls import set_tenant_context

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "cdp")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")
BATCH_SIZE = int(os.environ.get("CIR_BATCH_SIZE", "5000"))


def run_daily_identity_resolution() -> int:
    """Connects to Postgres and drains the staging table until empty.

    Returns:
        The total number of raw profiles processed across all batches.
    """
    conn = psycopg2.connect(
        host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, port=DB_PORT
    )
    total_processed = 0
    try:
        resolver = CustomerIdentityResolver(conn, schema=DB_SCHEMA, batch_size=BATCH_SIZE)
        logger.info("[%s] Starting daily identity resolution run.", datetime.now())

        while True:
            processed = resolver.run_resolution_batch()
            total_processed += processed
            if processed < BATCH_SIZE:
                break

        logger.info(
            "[%s] Daily run complete. Total profiles processed: %d",
            datetime.now(),
            total_processed,
        )
    finally:
        conn.close()

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
