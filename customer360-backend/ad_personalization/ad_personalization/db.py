"""Database connection helpers for the ad personalisation batch jobs.

Same psycopg2 + env-var config shape as campaign_activation/db.py (each code
location is independently deployable, so the config is duplicated rather than
imported). ``connect()`` opens one connection; callers own their transaction
scope. The session time zone is pinned to UTC so partition bounds and window
boundaries mean the same thing in every environment.

The ad tables live in the ``leo_ads`` schema (``ADS_DB_SCHEMA``), which has no
row-level security: every query in this package must filter by ``tenant_id``.
"""

import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "customer360")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")
DB_PORT = os.environ.get("DB_PORT", "5432")
ADS_DB_SCHEMA = os.environ.get("ADS_DB_SCHEMA", "leo_ads")


def connect():
    """Open a new psycopg2 connection to the customer360 database (UTC session)."""
    return psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
        options="-c timezone=UTC",
    )
