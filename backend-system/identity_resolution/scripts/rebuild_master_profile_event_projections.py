"""Rebuild every master-profile JSON event projection from S3 RAW objects.

Run this once after enabling the master-profile projection, and again after a
historical replay or source-link correction. Normal CIR batches rebuild only
the masters changed in that batch.
"""

from collections import defaultdict
import logging
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from identity_resolution.profile_event_projection import MasterProfileEventProjector  # noqa: E402
from identity_resolution.rls import set_tenant_context  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "customer360")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")


def main() -> None:
    connection = psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
    )
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            set_tenant_context(cursor, None)
            cursor.execute(
                f"""
                SELECT tenant_id, master_profile_id
                FROM {DB_SCHEMA}.cdp_master_profiles
                WHERE status_code = 1
                ORDER BY tenant_id, master_profile_id
                """
            )
            grouped: dict[str, set[str]] = defaultdict(set)
            for row in cursor.fetchall():
                grouped[str(row["tenant_id"])].add(str(row["master_profile_id"]))

        projector = MasterProfileEventProjector(connection, schema=DB_SCHEMA)
        for tenant_id, master_profile_ids in grouped.items():
            logger.info(
                "Rebuilding %d master-profile projections for tenant %s",
                len(master_profile_ids),
                tenant_id,
            )
            projector.project_profiles(tenant_id, master_profile_ids)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
