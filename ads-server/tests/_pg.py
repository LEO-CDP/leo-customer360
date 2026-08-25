"""
Shared helper for detecting whether a local PostgreSQL instance is reachable.

Used to decide whether repository/API integration tests should run against a
real database or be skipped (model tests never need this).
"""

import os


def check_postgres_available() -> bool:
    """Return True if PostgreSQL and the leo_ads schema are available."""
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=os.getenv("LEO_AD_DB_HOST", "localhost"),
            port=os.getenv("LEO_AD_DB_PORT", "5432"),
            user=os.getenv("LEO_AD_DB_USER", "postgres"),
            password=os.getenv("LEO_AD_DB_PASSWORD", "postgres"),
            dbname=os.getenv("LEO_AD_DB_NAME", "customer360"),
            connect_timeout=2,
        )
        with conn.cursor() as cursor:
            cursor.execute("SELECT to_regclass('leo_ads.tenant')")
            schema_ready = cursor.fetchone()[0] is not None
        conn.close()
        return schema_ready
    except Exception:
        return False
