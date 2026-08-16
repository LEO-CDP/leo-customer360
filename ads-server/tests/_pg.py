"""
Shared helper for detecting whether a local PostgreSQL instance is reachable.

Used to decide whether repository/API integration tests should run against a
real database or be skipped (model tests never need this).
"""

import os


def check_postgres_available() -> bool:
    """Return True if PostgreSQL is reachable with the current DB_* env vars."""
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "postgres"),
            dbname=os.getenv("DB_NAME", "customer360"),
            connect_timeout=2,
        )
        conn.close()
        return True
    except Exception:
        return False
