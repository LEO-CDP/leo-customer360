"""Database connection helpers for the campaign activation service.

Same psycopg2 + env-var config shape as segmentation/recompute.py (independent
deployable, so config is duplicated rather than imported). ``connect()`` opens a
single connection; callers manage their own transaction/savepoint scope.
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
DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")


def connect():
    """Open a new psycopg2 connection to the customer360 database."""
    return psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
    )
