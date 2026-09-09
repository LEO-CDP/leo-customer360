#!/usr/bin/env python3
"""Render the production Dagster instance config at container start.

PostgreSQL is mandatory for shared run/event/schedule state; S3-compatible
compute logs are mandatory when DAGSTER_REQUIRE_S3=true. A failed readiness
probe stops the orchestrator instead of silently losing production state.
Always writes a bounded QueuedRunCoordinator and run_monitoring so orphaned
runs are reaped instead of leaking their concurrency slot.

Run by entrypoint.sh before the Dagster process starts.
"""
from __future__ import annotations

import os
import sys

DAGSTER_HOME = os.environ.get("DAGSTER_HOME", "/dagster_home")
OUT = os.path.join(DAGSTER_HOME, "dagster.yaml")
DAGSTER_DB = os.environ.get("DAGSTER_PG_DB", "dagster")

# Concurrency cap and STARTING-run timeout; override via env.
MAX_CONCURRENT_RUNS = os.environ.get("DAGSTER_MAX_CONCURRENT_RUNS", "2")
RUN_START_TIMEOUT = os.environ.get("DAGSTER_RUN_START_TIMEOUT_SECONDS", "300")


def log(msg: str) -> None:
    print(f"[render-instance] {msg}", flush=True)


def postgres_ready() -> bool:
    """True if we can connect to the pre-provisioned dedicated Dagster DB."""
    host = os.environ.get("DB_HOST")
    if not host:
        return False
    try:
        import psycopg2
    except Exception as e:
        log(f"psycopg2 unavailable ({e})")
        return False
    base = dict(
        host=host,
        port=os.environ.get("DB_PORT", "5432"),
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
        log(f"postgres database is not reachable ({e})")
        return False


def s3_ready() -> bool:
    """True if the compute-log bucket is configured AND reachable."""
    endpoint = os.environ.get("DAGSTER_S3_ENDPOINT_URL") or os.environ.get("S3_ENDPOINT_URL") or os.environ.get("S3_ENDPOINT")
    bucket = os.environ.get("DAGSTER_LOGS_BUCKET") or os.environ.get("MINIO_BUCKET")
    key = (
        os.environ.get("DAGSTER_S3_ACCESS_KEY_ID")
        or os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("S3_ACCESS_KEY_ID")
        or os.environ.get("MINIO_ROOT_USER")
    )
    secret = (
        os.environ.get("DAGSTER_S3_SECRET_ACCESS_KEY")
        or os.environ.get("AWS_SECRET_ACCESS_KEY")
        or os.environ.get("S3_SECRET_ACCESS_KEY")
        or os.environ.get("MINIO_ROOT_PASSWORD")
    )
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
    endpoint_url: { env: S3_ENDPOINT_URL }
    skip_empty_files: true
"""

# Bounded run queue (always written, regardless of storage backend).
RUN_COORDINATOR_BLOCK = f"""run_coordinator:
  module: dagster.core.run_coordinator
  class: QueuedRunCoordinator
  config:
    max_concurrent_runs: {MAX_CONCURRENT_RUNS}
"""

# Reap orphaned runs so a dead worker cannot keep holding its slot.
RUN_MONITORING_BLOCK = f"""run_monitoring:
  enabled: true
  start_timeout_seconds: {RUN_START_TIMEOUT}
  cancel_timeout_seconds: 180
  max_resume_run_attempts: 0
  poll_interval_seconds: 60
"""

HEADER = (
    "# AUTO-GENERATED at container start by scripts/render_dagster_instance.py.\n"
    "# Shared PostgreSQL is mandatory; S3 compute logs are required in production.\n"
    "# Edit the renderer, not this file.\n"
)


def main() -> int:
    parts: list[str] = []
    if not postgres_ready():
        log("storage: PostgreSQL is unavailable; refusing to start")
        return 1
    parts.append(PG_BLOCK)
    log("storage: PostgreSQL (shared)")

    if s3_ready():
        parts.append(S3_BLOCK)
        log("compute logs: S3 / MinIO")
    elif os.environ.get("DAGSTER_REQUIRE_S3", "false").lower() in {"1", "true", "yes"}:
        log("compute logs: S3 / MinIO is required but unavailable; refusing to start")
        return 1
    else:
        log("compute logs: local (default)")

    # Always bound the queue and enable the orphaned-run reaper.
    parts.append(RUN_COORDINATOR_BLOCK)
    parts.append(RUN_MONITORING_BLOCK)
    log(f"run coordinator: QueuedRunCoordinator (max_concurrent_runs={MAX_CONCURRENT_RUNS})")
    log(f"run monitoring: enabled (start_timeout={RUN_START_TIMEOUT}s)")

    os.makedirs(DAGSTER_HOME, exist_ok=True)
    body = "\n".join(parts) if parts else "# all backends fell back to local defaults\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(HEADER + body)
    log(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"instance configuration failed: {e}")
        sys.exit(1)
