"""Aggregate data-tracking JSONL objects into hourly Redis and source totals."""

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "customer360")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")
DATA_SOURCE_LIMIT = int(os.environ.get("ANALYTICS_DATA_SOURCE_LIMIT", "10"))
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6580"))
REDIS_DB = int(os.environ.get("REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID")
S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY")
S3_SESSION_TOKEN = os.environ.get("S3_SESSION_TOKEN")
S3_FORCE_PATH_STYLE = os.environ.get("S3_FORCE_PATH_STYLE", "false").lower() == "true"

TRACKED_EVENT_FIELD = "tracked-event"
HOURLY_FOLDER_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}-\d{2})/(.+\.jsonl)$")
SOURCE_LOCK_PREFIX = "analytics:data-source-lock:"
SOURCE_STATE_PREFIX = "analytics:data-source-state:"
LOCK_TTL_SECONDS = int(os.environ.get("ANALYTICS_LOCK_TTL_SECONDS", "3600"))
_INCREMENT_IF_NEW_SCRIPT = """
if redis.call('SETNX', KEYS[2], ARGV[1]) == 1 then
    redis.call('HINCRBY', KEYS[1], ARGV[2], ARGV[3])
    return 1
end
return 0
"""
_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
_REFRESH_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


def set_tenant_context(cursor: Any, tenant_id: Optional[str]) -> None:
    """Set the transaction's RLS tenant context before tenant-scoped SQL."""
    value = str(tenant_id).strip() if tenant_id is not None else ""
    cursor.execute("SET app.tenant_id = %s", (value,))


def build_s3_client() -> Any:
    """Build an S3 or MinIO client from the shared environment settings."""
    import boto3
    from botocore.client import Config

    client_kwargs: dict[str, Any] = {
        "region_name": S3_REGION,
        "config": Config(s3={"addressing_style": "path" if S3_FORCE_PATH_STYLE else "auto"}),
    }
    if S3_ENDPOINT_URL:
        client_kwargs["endpoint_url"] = S3_ENDPOINT_URL
    if S3_ACCESS_KEY_ID:
        client_kwargs["aws_access_key_id"] = S3_ACCESS_KEY_ID
    if S3_SECRET_ACCESS_KEY:
        client_kwargs["aws_secret_access_key"] = S3_SECRET_ACCESS_KEY
    if S3_SESSION_TOKEN:
        client_kwargs["aws_session_token"] = S3_SESSION_TOKEN
    return boto3.client("s3", **client_kwargs)


def build_redis_client() -> Any:
    """Build the Redis client used by the aggregation job."""
    import redis

    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )


def connect_database() -> Any:
    """Open a PostgreSQL connection to the Customer 360 database."""
    import psycopg2

    return psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
    )


def fetch_data_sources(connection: Any, limit: int = DATA_SOURCE_LIMIT) -> list[tuple[str, str]]:
    """Return the first ``limit`` source IDs and tenant IDs from the catalog."""
    if limit <= 0:
        raise ValueError("data source limit must be positive")

    sources: list[tuple[str, str]] = []
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT tenant_id FROM {DB_SCHEMA}.sys_tenant ORDER BY tenant_id")
        tenant_ids = [str(row[0]) for row in cursor.fetchall()]
        for tenant_id in tenant_ids:
            set_tenant_context(cursor, tenant_id)
            cursor.execute(
                f"""
                SELECT data_source_id, tenant_id
                FROM {DB_SCHEMA}.sys_data_source
                ORDER BY data_source_id
                LIMIT %s
                """,
                (limit,),
            )
            sources.extend((str(row[0]), str(row[1])) for row in cursor.fetchall())

    # Fetching up to ``limit`` rows per tenant preserves the global top-N
    # result while keeping each tenant query bounded.
    sources.sort(key=lambda source: source[0])
    return sources[:limit]


def iter_hourly_objects(
    s3_client: Any,
    bucket: str,
    start_after: Optional[str] = None,
) -> list[tuple[str, str]]:
    """List JSONL object keys and their UTC hour folder in a bucket."""
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        objects: list[tuple[str, str]] = []
        paginate_kwargs: dict[str, str] = {"Bucket": bucket}
        if start_after:
            paginate_kwargs["StartAfter"] = start_after
        for page in paginator.paginate(**paginate_kwargs):
            for item in page.get("Contents", []):
                key = str(item.get("Key", ""))
                match = HOURLY_FOLDER_PATTERN.match(key)
                if match:
                    objects.append((match.group(1), key))
        return objects
    except Exception as exc:
        error_code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        if error_code in {"404", "NoSuchBucket", "NotFound"}:
            return []
        raise


def count_jsonl_records(body: Any, object_key: str) -> int:
    """Parse a JSONL body and return its number of non-empty JSON records."""
    count = 0
    lines = body.iter_lines() if hasattr(body, "iter_lines") else body
    for raw_line in lines:
        if not raw_line or not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid JSONL in object {object_key}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"JSONL record in object {object_key} must be an object")
        count += 1
    return count


def _source_lock_key(data_source_id: str) -> str:
    return f"{SOURCE_LOCK_PREFIX}{data_source_id}"


def _source_state_key(data_source_id: str) -> str:
    return f"{SOURCE_STATE_PREFIX}{data_source_id}"


def _set_source_state(redis_client: Any, data_source_id: str, **values: Any) -> None:
    values["updated_at"] = datetime.now(timezone.utc).isoformat()
    redis_client.hset(
        _source_state_key(data_source_id),
        mapping={key: str(value) for key, value in values.items() if value is not None},
    )


def acquire_source_lock(redis_client: Any, data_source_id: str, run_id: str) -> Optional[str]:
    """Acquire one source lease, returning its ownership token if available."""
    token = str(uuid4())
    acquired = redis_client.set(
        _source_lock_key(data_source_id), token, nx=True, ex=LOCK_TTL_SECONDS
    )
    if not acquired:
        return None
    _set_source_state(
        redis_client,
        data_source_id,
        status="running",
        run_id=run_id,
        started_at=datetime.now(timezone.utc).isoformat(),
        last_error="",
    )
    return token


def refresh_source_lock(redis_client: Any, data_source_id: str, token: str) -> None:
    """Extend a source lease and fail if another worker owns it."""
    refreshed = redis_client.eval(
        _REFRESH_LOCK_SCRIPT,
        1,
        _source_lock_key(data_source_id),
        token,
        str(LOCK_TTL_SECONDS),
    )
    if int(refreshed) != 1:
        raise RuntimeError(f"Analytics lock was lost for data source {data_source_id}")


def release_source_lock(redis_client: Any, data_source_id: str, token: str) -> None:
    """Release a source lease only when this run still owns it."""
    redis_client.eval(_RELEASE_LOCK_SCRIPT, 1, _source_lock_key(data_source_id), token)


def get_source_cursor(redis_client: Any, data_source_id: str) -> Optional[str]:
    """Return the last processed S3 object key for one source."""
    state = redis_client.hgetall(_source_state_key(data_source_id))
    return state.get("last_processed_object") or None


def save_source_cursor(redis_client: Any, data_source_id: str, hour: str, object_key: str) -> None:
    """Persist the hourly folder and object used as the next S3 StartAfter."""
    _set_source_state(
        redis_client,
        data_source_id,
        last_processed_hour=hour,
        last_processed_object=object_key,
    )


def get_source_statuses(redis_client: Any) -> list[dict[str, str]]:
    """Return persisted source states, marking active leases as running."""
    statuses: list[dict[str, str]] = []
    for state_key in redis_client.scan_iter(match=f"{SOURCE_STATE_PREFIX}*"):
        data_source_id = str(state_key)[len(SOURCE_STATE_PREFIX):]
        state = {str(key): str(value) for key, value in redis_client.hgetall(state_key).items()}
        if redis_client.exists(_source_lock_key(data_source_id)):
            state["status"] = "running"
        elif state.get("status") == "running":
            state["status"] = "stale"
        state["data_source_id"] = data_source_id
        statuses.append(state)
    return sorted(statuses, key=lambda status: status["data_source_id"])


def increment_hourly_count(
    redis_client: Any,
    data_source_id: str,
    hour: str,
    object_key: str,
    event_count: int,
) -> bool:
    """Atomically checkpoint an object and increment its hourly event hash.

    The checkpoint prevents retries from counting the same immutable S3 object
    twice. The Lua script performs ``HINCRBY`` only when the object is new.
    """
    if event_count < 0:
        raise ValueError("event count cannot be negative")

    hourly_key = f"{data_source_id}-{hour}"
    object_digest = hashlib.sha256(object_key.encode("utf-8")).hexdigest()
    checkpoint_key = f"analytics:processed-tracking-object:{data_source_id}:{object_digest}"
    result = redis_client.eval(
        _INCREMENT_IF_NEW_SCRIPT,
        2,
        hourly_key,
        checkpoint_key,
        "1",
        TRACKED_EVENT_FIELD,
        str(event_count),
    )
    return int(result) == 1


def update_data_source_total(
    connection: Any,
    tenant_id: str,
    data_source_id: str,
    increment: int,
) -> None:
    """Add newly processed events to one source's durable catalog total."""
    if increment <= 0:
        return

    with connection.cursor() as cursor:
        set_tenant_context(cursor, tenant_id)
        cursor.execute(
            f"""
            UPDATE {DB_SCHEMA}.sys_data_source
            SET total_tracked_event = COALESCE(total_tracked_event, 0) + %s,
                updated_at = NOW()
            WHERE data_source_id = %s AND tenant_id = %s
            """,
            (increment, data_source_id, tenant_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"Data source {data_source_id} was not found or is not accessible")
    connection.commit()


def process_tracking_logs(
    *,
    s3_client: Optional[Any] = None,
    redis_client: Optional[Any] = None,
    db_connection: Optional[Any] = None,
    data_source_limit: int = DATA_SOURCE_LIMIT,
    run_id: Optional[str] = None,
    log: Optional[Callable[..., None]] = None,
) -> dict[str, int]:
    """Process hourly JSONL logs for the catalog's first data sources.

    Each non-empty JSONL record represents one tracked event because the
    tracking API writes one record per event. Missing source buckets are empty
    sources, while malformed objects or dependency errors fail the run.
    """
    storage = s3_client if s3_client is not None else build_s3_client()
    cache = redis_client if redis_client is not None else build_redis_client()
    owns_connection = db_connection is None
    connection = db_connection or connect_database()
    write_log = log or logger.info
    run_id = run_id or str(uuid4())
    sources_processed = 0
    sources_skipped_running = 0
    objects_processed = 0
    events_added = 0

    try:
        for data_source_id, tenant_id in fetch_data_sources(connection, data_source_limit):
            lock_token = acquire_source_lock(cache, data_source_id, run_id)
            if lock_token is None:
                sources_skipped_running += 1
                write_log(
                    "Skipping data source %s because another analytics run owns its lock",
                    data_source_id,
                )
                continue

            source_increment = 0
            source_objects_processed = 0
            bucket = f"data-tracking-{data_source_id}"
            try:
                start_after = get_source_cursor(cache, data_source_id)
                for hour, object_key in iter_hourly_objects(
                    storage, bucket, start_after=start_after
                ):
                    refresh_source_lock(cache, data_source_id, lock_token)
                    response = storage.get_object(Bucket=bucket, Key=object_key)
                    body = response["Body"]
                    try:
                        event_count = count_jsonl_records(body, object_key)
                    finally:
                        close = getattr(body, "close", None)
                        if close:
                            close()

                    if increment_hourly_count(
                        cache, data_source_id, hour, object_key, event_count
                    ):
                        objects_processed += 1
                        source_objects_processed += 1
                        source_increment += event_count
                        write_log(
                            "Processed %s records from %s/%s",
                            event_count,
                            bucket,
                            object_key,
                        )
                    save_source_cursor(cache, data_source_id, hour, object_key)

                update_data_source_total(connection, tenant_id, data_source_id, source_increment)
                events_added += source_increment
                sources_processed += 1
                _set_source_state(
                    cache,
                    data_source_id,
                    status="completed",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    objects_processed=source_objects_processed,
                    events_added=source_increment,
                    last_error="",
                )
            except Exception as exc:
                _set_source_state(
                    cache,
                    data_source_id,
                    status="failed",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    last_error=str(exc),
                )
                raise
            finally:
                release_source_lock(cache, data_source_id, lock_token)

        return {
            "sources_processed": sources_processed,
            "sources_skipped_running": sources_skipped_running,
            "objects_processed": objects_processed,
            "events_added": events_added,
        }
    finally:
        if owns_connection:
            connection.close()
