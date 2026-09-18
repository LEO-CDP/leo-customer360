"""Database connection helpers for the notification engine.

Connection settings come from ``.config`` (single load_dotenv there). ``DB_SCHEMA``
is re-exported so callers can keep ``from .db import DB_SCHEMA, connect``.
"""

import psycopg2

from .config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_SCHEMA, DB_USER

__all__ = ["DB_SCHEMA", "connect"]


def connect():
    """Open a new psycopg2 connection to the customer360 database."""
    return psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
    )
