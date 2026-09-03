#!/usr/bin/env python3
"""Render $DAGSTER_HOME/dagster.yaml adaptively at container start.

The instance uses shared PostgreSQL storage + S3/MinIO compute logs WHEN THEY ARE
REACHABLE, and otherwise falls back to Dagster's local defaults (SQLite run/event/
schedule storage + local compute logs) so the orchestrator ALWAYS starts — on
local, UAT and PROD, with or without Postgres/S3.

Never raises: any probe failure just drops that backend to its local default.
Run by entrypoint.sh before the Dagster process starts.
"""
from __future__ import annotations

import os
import sys

DAGSTER_HOME = os.environ.get("DAGSTER_HOME", "/dagster_home")
OUT = os.path.join(DAGSTER_HOME, "dagster.yaml")
DAGSTER_DB = os.environ.get("DAGSTER_PG_DB", "dagster")


def log(msg: str) -> None:
    print(f"[render-instance] {msg}", flush=True)


def postgres_ready() -> bool:
    """True if we can connect to the dedicated dagster DB (creating it if needed)."""
    host = os.environ.get("DB_HOST")
    if not host:
        return False
    try:
        import psycopg2
    except Exception as e:  # driver missing -> local default
        log(f"psycopg2 unavailable ({e}); using SQLite")
        return False
    base = dict(
        host=host,
        port=int(os.environ.get("DB_PORT", "5432")),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
        connect_timeout=5,
    )
    # Best-effort: create the dedicated database if it does not exist yet.
    try:
        c = psycopg2.connect(dbname="postgres", **base)
        c.autocommit = True
        with c.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DAGSTER_DB,))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{DAGSTER_DB}"')
                log(f"created database {DAGSTER_DB!r}")
        c.close()
    except Exception as e:
        log(f"could not ensure database {DAGSTER_DB!r} ({e})")
    # Authoritative check: can we actually connect to the target database?
    try:
        psycopg2.connect(dbname=DAGSTER_DB, **base).close()
        return True
    except Exception as e:
        log(f"postgres not reachable ({e}); using SQLite")
        return False


def s3_ready() -> bool:
    """True if the compute-log bucket is configured AND reachable."""
    endpoint = os.environ.get("S3_ENDPOINT")
    bucket = os.environ.get("MINIO_BUCKET")
    key = os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("MINIO_ROOT_USER")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY") or os.environ.get("MINIO_ROOT_PASSWORD")
    if not (endpoint and bucket and key and secret):
        return False
    try:
        import boto3
        from botocore.config import Config

        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            region_name=os.environ.get("S3_REGION", "us-east-1"),
            config=Config(
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=5,
                retries={"max_attempts": 0},
            ),
        )
        s3.head_bucket(Bucket=bucket)
        return True
    except Exception as e:
        log(f"S3 bucket not reachable ({e}); using local compute logs")
        return False


PG_BLOCK = f"""storage:
  postgres:
    postgres_db:
      username: {{ env: DB_USER }}
      password: {{ env: DB_PASSWORD }}
      hostname: {{ env: DB_HOST }}
      db_name: {DAGSTER_DB}
      port: {{ env: DB_PORT }}
"""

S3_BLOCK = """compute_logs:
  module: dagster_aws.s3.compute_log_manager
  class: S3ComputeLogManager
  config:
    bucket: { env: MINIO_BUCKET }
    prefix: dagster-compute-logs
    endpoint_url: { env: S3_ENDPOINT }
    skip_empty_files: true
"""

HEADER = (
    "# AUTO-GENERATED at container start by scripts/render_dagster_instance.py.\n"
    "# Adaptive: shared PostgreSQL + S3 compute logs when reachable, else local\n"
    "# SQLite + local compute logs. Edit the renderer, not this file.\n"
)


def main() -> int:
    parts: list[str] = []
    if postgres_ready():
        parts.append(PG_BLOCK)
        log("storage: PostgreSQL (shared)")
    else:
        log("storage: SQLite (local default)")
    if s3_ready():
        parts.append(S3_BLOCK)
        log("compute logs: S3 / MinIO")
    else:
        log("compute logs: local (default)")

    os.makedirs(DAGSTER_HOME, exist_ok=True)
    body = "\n".join(parts) if parts else "# all backends fell back to local defaults\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(HEADER + body)
    log(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # never block Dagster startup
        log(f"unexpected error ({e}); leaving Dagster on local defaults")
        try:
            with open(OUT, "w", encoding="utf-8") as f:
                f.write("# render failed; using Dagster local defaults (SQLite + local logs)\n")
        except Exception:
            pass
        sys.exit(0)
