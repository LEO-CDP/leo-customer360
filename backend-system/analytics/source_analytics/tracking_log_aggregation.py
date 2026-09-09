"""Aggregate data-tracking JSONL objects into hourly Redis and source totals."""

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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
# Non-positive means "no cap": process all active sources across all tenants.
DATA_SOURCE_LIMIT = int(os.environ.get("ANALYTICS_DATA_SOURCE_LIMIT", "0"))
MAX_WORKERS = int(os.environ.get("ANALYTICS_MAX_WORKERS", "8"))
DATAFRAME_ENGINE = os.environ.get("ANALYTICS_DATAFRAME_ENGINE", "auto").strip().lower()
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6580"))
REDIS_DB = int(os.environ.get("REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD")
# Host-run local Dagster uses the published MinIO port, while containerized
# deployments can continue to use the shared S3_ENDPOINT_URL directly.
S3_ENDPOINT_URL = os.environ.get("ANALYTICS_S3_ENDPOINT_URL") or os.environ.get("S3_ENDPOINT_URL")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID")
S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY")
S3_SESSION_TOKEN = os.environ.get("S3_SESSION_TOKEN")
S3_FORCE_PATH_STYLE = os.environ.get("S3_FORCE_PATH_STYLE", "false").lower() == "true"
S3_MAX_POOL_CONNECTIONS = int(os.environ.get("ANALYTICS_S3_MAX_POOL_CONNECTIONS", "64"))

TRACKED_EVENT_FIELD = "tracked-event"
HOURLY_FOLDER_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}-\d{2})/(.+\.jsonl)$")
SOURCE_LOCK_PREFIX = "analytics:data-source-lock:"
SOURCE_STATE_PREFIX = "analytics:data-source-state:"
SOURCE_DAILY_PREFIX = "analytics:data-source-daily:"
SOURCE_PROFILE_HLL_PREFIX = "analytics:data-source-profiles-hll:"
LOCK_TTL_SECONDS = int(os.environ.get("ANALYTICS_LOCK_TTL_SECONDS", "3600"))
PROCESSED_OBJECT_TTL_SECONDS = int(
    os.environ.get("ANALYTICS_PROCESSED_OBJECT_TTL_SECONDS", str(48 * 60 * 60))
)
_INCREMENT_IF_NEW_SCRIPT = """
if redis.call('SET', KEYS[2], ARGV[1], 'NX', 'EX', ARGV[4]) then
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


def _load_pandas() -> Any:
    """Load pandas lazily so import cost only occurs during aggregation."""
    import pandas as pd

    return pd


def _load_polars() -> Any:
    """Load polars lazily so import cost only occurs during aggregation."""
    import polars as pl

    return pl


def _resolve_dataframe_engine(engine: Optional[str] = None) -> str:
    """Resolve dataframe engine from argument or environment."""
    selected = (engine or DATAFRAME_ENGINE or "auto").strip().lower()
    if selected not in {"auto", "pandas", "polars"}:
        return "auto"
    return selected


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
        "config": Config(
            s3={"addressing_style": "path" if S3_FORCE_PATH_STYLE else "auto"},
            max_pool_connections=max(8, S3_MAX_POOL_CONNECTIONS),
        ),
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
    """Return active source IDs and tenant IDs across all tenants.

    When ``limit`` is positive, the final list is globally capped to that size.
    A non-positive ``limit`` means no global cap.
    """

    sources: list[tuple[str, str]] = []
    unlimited = limit <= 0
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT tenant_id FROM {DB_SCHEMA}.sys_tenant ORDER BY tenant_id")
        tenant_ids = [str(row[0]) for row in cursor.fetchall()]
        for tenant_id in tenant_ids:
            set_tenant_context(cursor, tenant_id)
            query = f"""
                SELECT data_source_id, tenant_id
                FROM {DB_SCHEMA}.sys_data_source
                WHERE tenant_id = %s AND status = 1
                ORDER BY data_source_id
            """
            params: tuple[Any, ...] = (tenant_id,)
            if not unlimited:
                query += " LIMIT %s"
                params = (tenant_id, limit)
            cursor.execute(
                query,
                params,
            )
            sources.extend((str(row[0]), str(row[1])) for row in cursor.fetchall())

    # Preserve deterministic ordering across tenants regardless of worker count.
    sources.sort(key=lambda source: source[0])
    if unlimited:
        return sources
    return sources[:limit]


def iter_hourly_objects(
    s3_client: Any,
    bucket: str,
    start_after: Optional[str] = None,
    prefix: Optional[str] = None,
) -> Any:
    """List JSONL object keys and their UTC hour folder in a bucket."""
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        paginate_kwargs: dict[str, str] = {"Bucket": bucket}
        if prefix:
            paginate_kwargs["Prefix"] = prefix
        if start_after:
            paginate_kwargs["StartAfter"] = start_after
        for page in paginator.paginate(**paginate_kwargs):
            for item in page.get("Contents", []):
                key = str(item.get("Key", ""))
                match = HOURLY_FOLDER_PATTERN.match(key)
                if match:
                    yield match.group(1), key
    except Exception as exc:
        error_code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        if error_code in {"404", "NoSuchBucket", "NotFound"}:
            return
        raise


def _source_daily_key(data_source_id: str) -> str:
    return f"{SOURCE_DAILY_PREFIX}{data_source_id}"


def _source_profile_hll_key(data_source_id: str) -> str:
    return f"{SOURCE_PROFILE_HLL_PREFIX}{data_source_id}"


def current_system_gmt_hour() -> str:
    """Return current system datetime in GMT with format yyyy-mm-dd-HH."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")


def s3_json_cache_key(bucket: str, object_key: str) -> str:
    """Build the Redis cache key from the S3 JSON object path."""
    return f"s3://{bucket}/{object_key}"


def _get_source_state_int(redis_client: Any, data_source_id: str, field: str) -> Optional[int]:
    raw = redis_client.hgetall(_source_state_key(data_source_id)).get(field)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _increment_source_cached_total(redis_client: Any, data_source_id: str, increment: int) -> None:
    if increment <= 0:
        return
    state_key = _source_state_key(data_source_id)
    state = redis_client.hgetall(state_key)
    current = int(state.get("total_tracked_event_cache", "0") or "0")
    redis_client.hset(state_key, mapping={"total_tracked_event_cache": str(current + increment)})


def _increment_source_daily_total(redis_client: Any, data_source_id: str, day: str, increment: int) -> None:
    if increment <= 0:
        return
    daily_key = _source_daily_key(data_source_id)
    daily = redis_client.hgetall(daily_key)
    current = int(daily.get(day, "0") or "0")
    redis_client.hset(daily_key, mapping={day: str(current + increment)})


def _add_source_profile_signatures(
    redis_client: Any,
    data_source_id: str,
    signatures: set[str],
) -> None:
    if not signatures:
        return
    redis_client.pfadd(_source_profile_hll_key(data_source_id), *sorted(signatures))


def _get_daily_stats(
    redis_client: Any,
    data_source_id: str,
    engine: Optional[str] = None,
) -> tuple[int, int]:
    daily = redis_client.hgetall(_source_daily_key(data_source_id))
    if not daily:
        return 0, 0

    selected_engine = _resolve_dataframe_engine(engine)

    if selected_engine in {"auto", "polars"}:
        try:
            pl = _load_polars()
            frame = pl.DataFrame(
                {
                    "day": list(daily.keys()),
                    "events": [str(value) for value in daily.values()],
                }
            ).with_columns(
                pl.col("events").cast(pl.Int64, strict=False).fill_null(0)
            )

            # Hybrid pipeline pattern:
            # 1) Use Polars for fast normalization/casting on larger inputs.
            # 2) Convert to Pandas for downstream/library-friendly calculations.
            pd = _load_pandas()
            pandas_frame = pd.DataFrame(frame.to_dicts())
            return int(pandas_frame["day"].nunique()), int(pandas_frame["events"].sum())
        except Exception:
            if selected_engine == "polars":
                raise

    if selected_engine in {"auto", "pandas"}:
        try:
            pd = _load_pandas()
            frame = pd.DataFrame(
                {
                    "day": list(daily.keys()),
                    "events": pd.to_numeric(list(daily.values()), errors="coerce"),
                }
            )
            frame["events"] = frame["events"].fillna(0).astype("int64")
            return int(frame["day"].nunique()), int(frame["events"].sum())
        except Exception:
            if selected_engine == "pandas":
                raise

    total = 0
    for value in daily.values():
        try:
            total += int(value)
        except (TypeError, ValueError):
            continue
    return len(daily), total


def _aggregate_source_results(
    results: list[dict[str, Any]],
    engine: Optional[str] = None,
) -> dict[str, int]:
    """Aggregate per-source worker results with pandas for large source counts."""
    if not results:
        return {
            "sources_processed": 0,
            "sources_skipped_running": 0,
            "objects_processed": 0,
            "events_added": 0,
            "sources_total": 0,
        }

    selected_engine = _resolve_dataframe_engine(engine)

    if selected_engine in {"auto", "polars"}:
        try:
            pl = _load_polars()
            frame = pl.DataFrame(results).with_columns(
                [
                    pl.col("skipped_running").cast(pl.Boolean, strict=False).fill_null(False),
                    pl.col("objects_processed").cast(pl.Int64, strict=False).fill_null(0),
                    pl.col("events_added").cast(pl.Int64, strict=False).fill_null(0),
                ]
            )

            # Hybrid pipeline pattern:
            # Polars handles schema coercion efficiently; Pandas handles final
            # aggregation in a format expected by downstream Python tooling.
            pd = _load_pandas()
            pandas_frame = pd.DataFrame(frame.to_dicts())
            skipped = pandas_frame["skipped_running"].astype(bool)
            return {
                "sources_processed": int((~skipped).sum()),
                "sources_skipped_running": int(skipped.sum()),
                "objects_processed": int(pandas_frame["objects_processed"].fillna(0).sum()),
                "events_added": int(pandas_frame["events_added"].fillna(0).sum()),
                "sources_total": int(len(pandas_frame.index)),
            }
        except Exception:
            if selected_engine == "polars":
                raise

    if selected_engine in {"auto", "pandas"}:
        try:
            pd = _load_pandas()
            frame = pd.DataFrame(results)
            skipped = frame["skipped_running"].astype(bool)
            return {
                "sources_processed": int((~skipped).sum()),
                "sources_skipped_running": int(skipped.sum()),
                "objects_processed": int(frame["objects_processed"].fillna(0).sum()),
                "events_added": int(frame["events_added"].fillna(0).sum()),
                "sources_total": int(len(frame.index)),
            }
        except Exception:
            if selected_engine == "pandas":
                raise

    sources_skipped_running = sum(1 for item in results if item.get("skipped_running"))
    return {
        "sources_processed": len(results) - sources_skipped_running,
        "sources_skipped_running": sources_skipped_running,
        "objects_processed": sum(int(item.get("objects_processed", 0)) for item in results),
        "events_added": sum(int(item.get("events_added", 0)) for item in results),
        "sources_total": len(results),
    }


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


def _extract_profile_signature(record: dict[str, Any]) -> Optional[str]:
    """Extract a stable profile signature from one NDJSON tracking record."""
    event = record.get("event")
    if not isinstance(event, dict):
        return None

    def normalize(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text.lower()

    direct_keys = [
        "external_customer_id",
        "user_id",
        "email",
        "phone_number",
        "device_id",
        "advertising_id",
        "cookie_id",
        "session_id",
    ]
    for key in direct_keys:
        normalized = normalize(event.get(key))
        if normalized:
            return f"{key}:{normalized}"

    identities = event.get("profile_identities")
    if isinstance(identities, dict):
        for key in direct_keys:
            normalized = normalize(identities.get(key))
            if normalized:
                return f"{key}:{normalized}"

    return None


def summarize_bucket_metrics(s3_client: Any, bucket: str) -> tuple[int, int, float]:
    """Recompute source totals and averages by scanning all hourly JSONL logs."""
    total_tracked_event = 0
    days_with_events: set[str] = set()
    profile_signatures: set[str] = set()

    for hour, object_key in iter_hourly_objects(s3_client, bucket):
        response = s3_client.get_object(Bucket=bucket, Key=object_key)
        body = response["Body"]
        object_count = 0
        try:
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
                object_count += 1
                signature = _extract_profile_signature(record)
                if signature:
                    profile_signatures.add(signature)
        finally:
            close = getattr(body, "close", None)
            if close:
                close()

        if object_count > 0:
            days_with_events.add(hour[:10])
            total_tracked_event += object_count

    avg_daily_event = round(total_tracked_event / len(days_with_events)) if days_with_events else 0
    avg_events_per_profile = (
        round(total_tracked_event / len(profile_signatures), 2)
        if profile_signatures
        else 0.0
    )
    return total_tracked_event, avg_daily_event, avg_events_per_profile


def count_records_and_signatures(body: Any, object_key: str) -> tuple[int, set[str]]:
    """Parse one JSONL object and return event count plus profile signatures."""
    count = 0
    signatures: set[str] = set()
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
        signature = _extract_profile_signature(record)
        if signature:
            signatures.add(signature)
    return count, signatures


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
    bucket: str,
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
    checkpoint_key = s3_json_cache_key(bucket, object_key)
    processed_at = current_system_gmt_hour()
    result = redis_client.eval(
        _INCREMENT_IF_NEW_SCRIPT,
        2,
        hourly_key,
        checkpoint_key,
        processed_at,
        TRACKED_EVENT_FIELD,
        str(event_count),
        str(PROCESSED_OBJECT_TTL_SECONDS),
    )
    return int(result) == 1


def update_data_source_summary(
    connection: Any,
    tenant_id: str,
    data_source_id: str,
    total_tracked_event: int,
    avg_daily_event: int,
    avg_events_per_profile: float,
) -> None:
    """Persist core summary metrics for one active data source."""
    if total_tracked_event < 0:
        raise ValueError("total tracked event cannot be negative")

    with connection.cursor() as cursor:
        set_tenant_context(cursor, tenant_id)
        cursor.execute(
            f"""
            UPDATE {DB_SCHEMA}.sys_data_source
            SET total_tracked_event = %s,
                avg_daily_event = %s,
                avg_events_per_profile = %s,
                updated_at = NOW()
            WHERE data_source_id = %s AND tenant_id = %s AND status = 1
            """,
            (
                total_tracked_event,
                avg_daily_event,
                avg_events_per_profile,
                data_source_id,
                tenant_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(
                f"Active data source {data_source_id} was not found or is not accessible"
            )
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
    write_log = log or logger.info
    run_id = run_id or str(uuid4())
    current_hour = current_system_gmt_hour()
    source_items: list[tuple[str, str]] = []
    source_results: list[dict[str, Any]] = []

    def _process_one_source(data_source_id: str, tenant_id: str) -> dict[str, Any]:
        lock_token = acquire_source_lock(cache, data_source_id, run_id)
        if lock_token is None:
            write_log(
                "Skipping data source %s because another analytics run owns its lock",
                data_source_id,
            )
            return {
                "data_source_id": data_source_id,
                "tenant_id": tenant_id,
                "skipped_running": True,
                "objects_processed": 0,
                "events_added": 0,
            }

        source_increment = 0
        source_objects_processed = 0
        saw_checkpointed_object = False
        bucket = f"data-tracking-{data_source_id}"
        try:
            start_after = get_source_cursor(cache, data_source_id)
            if not start_after or not start_after.startswith(f"{current_hour}/"):
                start_after = None
            for hour, object_key in iter_hourly_objects(
                storage,
                bucket,
                start_after=start_after,
                prefix=f"{current_hour}/",
            ):
                refresh_source_lock(cache, data_source_id, lock_token)
                response = storage.get_object(Bucket=bucket, Key=object_key)
                body = response["Body"]
                try:
                    event_count, signatures = count_records_and_signatures(body, object_key)
                finally:
                    close = getattr(body, "close", None)
                    if close:
                        close()

                if increment_hourly_count(
                    cache,
                    data_source_id,
                    hour,
                    bucket,
                    object_key,
                    event_count,
                ):
                    source_objects_processed += 1
                    source_increment += event_count
                    _increment_source_cached_total(cache, data_source_id, event_count)
                    _increment_source_daily_total(cache, data_source_id, hour[:10], event_count)
                    _add_source_profile_signatures(cache, data_source_id, signatures)
                    write_log(
                        "Processed %s records from %s/%s",
                        event_count,
                        bucket,
                        object_key,
                    )
                else:
                    saw_checkpointed_object = True
                save_source_cursor(cache, data_source_id, hour, object_key)

            total_tracked_event = _get_source_state_int(
                cache, data_source_id, "total_tracked_event_cache"
            )
            if total_tracked_event is None and saw_checkpointed_object:
                # Recovery path: state was lost but checkpoints existed, so rebuild once.
                (
                    total_tracked_event,
                    _recovered_avg_daily,
                    _recovered_avg_events_per_profile,
                ) = summarize_bucket_metrics(storage, bucket)
                cache.hset(
                    _source_state_key(data_source_id),
                    mapping={"total_tracked_event_cache": str(total_tracked_event)},
                )
            elif total_tracked_event is None:
                total_tracked_event = 0

            active_days, daily_total = _get_daily_stats(cache, data_source_id)
            profile_count = int(cache.pfcount(_source_profile_hll_key(data_source_id)))
            avg_daily_event = round(daily_total / active_days) if active_days > 0 else 0
            avg_events_per_profile = (
                round(total_tracked_event / profile_count, 2)
                if profile_count > 0
                else 0.0
            )

            # One DB connection per worker keeps writes thread-safe under psycopg2.
            # In single-thread test mode, reuse the injected connection.
            if db_connection is not None:
                update_data_source_summary(
                    db_connection,
                    tenant_id,
                    data_source_id,
                    total_tracked_event,
                    avg_daily_event,
                    avg_events_per_profile,
                )
            else:
                worker_connection = connect_database()
                try:
                    update_data_source_summary(
                        worker_connection,
                        tenant_id,
                        data_source_id,
                        total_tracked_event,
                        avg_daily_event,
                        avg_events_per_profile,
                    )
                finally:
                    worker_connection.close()

            _set_source_state(
                cache,
                data_source_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                objects_processed=source_objects_processed,
                events_added=source_increment,
                last_error="",
            )
            return {
                "data_source_id": data_source_id,
                "tenant_id": tenant_id,
                "skipped_running": False,
                "objects_processed": source_objects_processed,
                "events_added": source_increment,
            }
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

    if db_connection is not None:
        source_items = fetch_data_sources(db_connection, data_source_limit)
        for data_source_id, tenant_id in source_items:
            result = _process_one_source(data_source_id, tenant_id)
            source_results.append(result)
    else:
        seed_connection = connect_database()
        try:
            source_items = fetch_data_sources(seed_connection, data_source_limit)
        finally:
            seed_connection.close()

        if source_items:
            worker_count = max(1, min(MAX_WORKERS, len(source_items)))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(_process_one_source, data_source_id, tenant_id): (
                        data_source_id,
                        tenant_id,
                    )
                    for data_source_id, tenant_id in source_items
                }
                for future in as_completed(futures):
                    result = future.result()
                    source_results.append(result)

    return _aggregate_source_results(source_results)
